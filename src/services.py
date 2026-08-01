from __future__ import annotations

import html
import logging
import time

from src.command_router import Intent, classify
from src.gateways import MatrixGateway, MaxGateway, MediaRelayService
from src.linking import generate_link_code, is_expired
from src.rate_limit import SlidingWindowLimiter
from src.strings import Strings

logger = logging.getLogger(__name__)


def _matrix_sender_display_name(room, sender_id: str) -> str:
    user = room.users.get(sender_id)
    if user is not None:
        return user.name
    return sender_id


def _format_relay_max_text(username: str, body: str) -> str:
    u = username.strip() or "?"
    return f"{u}: {body}"


def _format_relay_matrix_content(username: str, body: str) -> dict:
    u_plain = username.strip() or "?"
    plain = f"{u_plain}: {body}"
    u = html.escape(u_plain, quote=True)
    b = html.escape(body, quote=True)
    formatted = f"<b>{u}</b>: {b}"
    return {
        "msgtype": "m.text",
        "body": plain,
        "format": "org.matrix.custom.html",
        "formatted_body": formatted,
    }


class AuthorizationService:
    MODERATOR_LEVEL = 50
    ADMIN_LEVEL = 100

    def __init__(self, max_gateway: MaxGateway) -> None:
        self.max_gateway = max_gateway

    @staticmethod
    def _user_level(room, sender_id: str) -> int | None:
        """Возвращает power-level пользователя или None при отсутствии данных."""
        try:
            return int(room.power_levels.get_user_level(sender_id))
        except AttributeError, KeyError, TypeError, ValueError:
            logger.warning(
                "Отсутствуют данные о power-level для отправителя=%s, считаем 0",
                sender_id,
            )
            return None

    @classmethod
    def is_moderator(cls, room, sender_id: str) -> bool:
        level = cls._user_level(room, sender_id)
        if level is None:
            return False
        return level >= cls.MODERATOR_LEVEL

    @classmethod
    def is_admin(cls, room, sender_id: str) -> bool:
        """Строгая проверка: права администратора Matrix (power-level >= 100)."""
        level = cls._user_level(room, sender_id)
        if level is None:
            return False
        return level >= cls.ADMIN_LEVEL

    async def is_max_admin(self, max_chat_id: int, user_id: int) -> bool:
        try:
            member = await self.max_gateway.get_chat_member(max_chat_id, user_id)
        except Exception:
            # Сетевой/транспортный сбой - отказываем (fail closed), но логируем
            # заорем как резанные, чтобы отличить от обычного не-админа.
            logger.exception(
                "Не удалось проверить статус админа участника чата Max chat_id=%s user=%s (отказ)",
                max_chat_id,
                user_id,
            )
            return False
        if member is None:
            return False
        return bool(member.is_owner or member.is_admin)


class HostnamePolicy:
    @staticmethod
    def hostname_from_server_name(server_name: str) -> str:
        s = (server_name or "").strip().lower()
        if not s:
            return ""
        if s.startswith("["):
            end = s.find("]")
            if end == -1:
                return s
            return s[1:end]
        if ":" in s:
            host, maybe_port = s.rsplit(":", 1)
            if maybe_port.isdigit():
                return host
        return s

    @staticmethod
    def server_name_from_mxid(mxid: str) -> str | None:
        if ":" not in mxid:
            return None
        return mxid.split(":", 1)[1].strip()

    @staticmethod
    def is_allowed_hostname(hostname: str, allowed: str | None) -> bool:
        allowed_norm = (allowed or "").strip().lower()
        if not allowed_norm:
            return True
        h = (hostname or "").strip().lower()
        if not h:
            return False
        return h == allowed_norm or h.endswith(f".{allowed_norm}")

    @classmethod
    async def room_server_allowed(
        cls, matrix_gateway: MatrixGateway, allowed: str | None, room_id: str
    ) -> bool:
        if not allowed:
            return True
        room_server = room_id.split(":", 1)[1].strip() if ":" in room_id else ""
        if room_server:
            return cls.is_allowed_hostname(cls.hostname_from_server_name(room_server), allowed)
        room = matrix_gateway.rooms.get(room_id)
        creator = room.creator if room else ""
        creator_server = cls.server_name_from_mxid(creator) if creator else None
        if creator_server:
            return cls.is_allowed_hostname(cls.hostname_from_server_name(creator_server), allowed)
        logger.warning(
            "Не удалось определить сервер комнаты для проверки MATRIX_ALLOWED_SERVER, "
            "отказ: room_id=%s creator=%s",
            room_id,
            creator,
        )
        return False


class LinkingService:
    def __init__(
        self,
        db,
        matrix_gateway: MatrixGateway,
        max_gateway: MaxGateway,
        auth: AuthorizationService,
        strings: Strings,
        settings,
    ) -> None:
        self.db = db
        self.matrix_gateway = matrix_gateway
        self.max_gateway = max_gateway
        self.auth = auth
        self.strings = strings
        self.settings = settings
        self._link_attempts = SlidingWindowLimiter(
            settings.rate_limit_link_attempts,
            float(settings.rate_limit_link_window_seconds),
            global_max=settings.rate_limit_link_attempts * 10,
        )
        self._code_gen = SlidingWindowLimiter(
            settings.rate_limit_code_generations,
            float(settings.rate_limit_code_window_seconds),
            global_max=settings.rate_limit_code_generations * 10,
        )

    async def issue_link_for_max_chat(self, max_chat_id: int, *, started_at_ms: int) -> None:
        if not self._code_gen.allow(max_chat_id):
            await self.max_gateway.send_text(max_chat_id, self.strings.rate_limit_code)
            return
        if await self.db.get_bridge_by_max(max_chat_id):
            await self.max_gateway.send_text(max_chat_id, self.strings.already_linked_max)
            return
        await self.db.cleanup_expired_pending()
        await self.db.revoke_pending_for_max(max_chat_id)
        code = generate_link_code()
        expires = time.time() + float(self.settings.link_code_ttl_seconds)
        await self.db.insert_pending(code, max_chat_id, expires)
        await self.max_gateway.send_text(
            max_chat_id, self.strings.max_welcome(self.settings.matrix_user_id, code)
        )

    async def try_link_from_matrix(self, room, sender_id: str, raw_body: str) -> None:
        room_id = room.room_id
        intent, code = classify(raw_body)
        if intent == Intent.UNLINK:
            await self.unlink_from_matrix(room, sender_id)
            return
        if intent != Intent.LINK or not code:
            return
        if not self._link_attempts.allow(room_id):
            await self.matrix_gateway.send_text(room_id, self.strings.rate_limit_link)
            return
        if not await HostnamePolicy.room_server_allowed(
            self.matrix_gateway, self.settings.matrix_allowed_server, room_id
        ):
            logger.warning(
                "Привязка отклонена по MATRIX_ALLOWED_SERVER. room_id=%s allowed_domain=%s",
                room_id,
                self.settings.matrix_allowed_server,
            )
            return
        if await self.matrix_gateway.is_room_encrypted(room_id):
            await self.matrix_gateway.send_text(room_id, self.strings.encrypted_room_link_denied)
            return
        if getattr(self.settings, "require_mod_to_link", False) and not self.auth.is_admin(
            room, sender_id
        ):
            await self.matrix_gateway.send_text(room_id, self.strings.not_authorized)
            return
        if await self.db.get_bridge_by_matrix(room_id):
            await self.matrix_gateway.send_text(room_id, self.strings.already_linked_matrix)
            return
        pending = await self.db.get_pending_by_code(code)
        if pending is None:
            await self.matrix_gateway.send_text(room_id, self.strings.code_not_found)
            return
        if is_expired(pending.expires_at):
            await self.db.delete_pending(code)
            await self.matrix_gateway.send_text(room_id, self.strings.code_expired)
            return
        linked = await self.db.try_link_atomic(code, pending.max_chat_id, room_id)
        if not linked:
            await self.matrix_gateway.send_text(room_id, self.strings.max_chat_already_linked)
            return
        await self.matrix_gateway.send_text(room_id, self.strings.link_success_matrix)
        try:
            await self.max_gateway.send_text(pending.max_chat_id, self.strings.link_success_max)
        except Exception as exc:
            logger.error("Не удалось уведомить Max после привязки: %s", exc)

    async def unlink_from_matrix(self, room, sender_id: str) -> None:
        room_id = room.room_id
        if not self.auth.is_moderator(room, sender_id):
            await self.matrix_gateway.send_text(room_id, self.strings.not_authorized)
            return
        bridge = await self.db.get_bridge_by_matrix(room_id)
        if not bridge:
            await self.matrix_gateway.send_text(room_id, self.strings.unlink_no_bridge_matrix)
            return
        removed = await self.db.delete_bridge_by_matrix(room_id)
        if not removed:
            await self.matrix_gateway.send_text(room_id, self.strings.unlink_failed_matrix)
            return
        await self.matrix_gateway.send_text(room_id, self.strings.unlink_success_matrix)
        try:
            await self.max_gateway.send_text(bridge.max_chat_id, self.strings.unlink_success_max)
        except Exception:
            logger.exception("Не удалось уведомить Max после отвязки")

    async def unlink_from_max(self, max_chat_id: int, from_user_id: int) -> None:
        if not await self.auth.is_max_admin(max_chat_id, from_user_id):
            await self.max_gateway.send_text(max_chat_id, self.strings.not_authorized)
            return
        bridge = await self.db.get_bridge_by_max(max_chat_id)
        if not bridge:
            await self.max_gateway.send_text(max_chat_id, self.strings.unlink_no_bridge_max)
            return
        removed = await self.db.delete_bridge_by_max(max_chat_id)
        if not removed:
            await self.max_gateway.send_text(max_chat_id, self.strings.unlink_failed_max)
            return
        await self.max_gateway.send_text(max_chat_id, self.strings.unlink_success_max)
        try:
            await self.matrix_gateway.send_text(
                bridge.matrix_room_id, self.strings.unlink_success_matrix
            )
        except Exception:
            logger.exception("Не удалось уведомить Matrix после отвязки")

    async def maybe_send_matrix_welcome(self, room_id: str) -> None:
        if await self.db.is_welcome_sent(room_id):
            return
        if await self.matrix_gateway.is_room_encrypted(room_id):
            await self.matrix_gateway.send_text(room_id, self.strings.encrypted_room)
            await self.db.mark_welcome_sent(room_id)
            return
        await self.matrix_gateway.send_text(room_id, self.strings.matrix_welcome)
        await self.db.mark_welcome_sent(room_id)

    async def on_bot_joined_matrix_room(self, room_id: str) -> None:
        if not await HostnamePolicy.room_server_allowed(
            self.matrix_gateway, self.settings.matrix_allowed_server, room_id
        ):
            logger.warning("Пропуск приветствия для комнаты вне разрешённого сервера: %s", room_id)
            return
        await self.maybe_send_matrix_welcome(room_id)


class RelayService:
    def __init__(
        self,
        db,
        matrix_gateway: MatrixGateway,
        max_gateway: MaxGateway,
        media: MediaRelayService,
        strings: Strings,
        started_at_ms: int,
    ) -> None:
        self.db = db
        self.matrix_gateway = matrix_gateway
        self.max_gateway = max_gateway
        self.media = media
        self.strings = strings
        self.started_at_ms = started_at_ms

    _FRESHNESS_TOLERANCE_MS = 60_000

    def is_fresh_matrix_event(self, server_ts_ms: int | None) -> bool:
        if server_ts_ms is None:
            return True
        return int(server_ts_ms) >= self.started_at_ms - self._FRESHNESS_TOLERANCE_MS

    async def relay_matrix_to_max(
        self,
        room,
        sender_id: str,
        body: str,
        *,
        server_ts_ms: int | None = None,
        event_id: str | None = None,
    ) -> None:
        room_id = room.room_id
        if classify(body)[0] != Intent.RELAY:
            return
        if not self.is_fresh_matrix_event(server_ts_ms):
            return
        bridge = await self.db.get_bridge_by_matrix(room_id)
        if not bridge:
            return
        if sender_id == self.matrix_gateway.user_id:
            return
        if event_id and await self.db.was_relayed(event_id):
            logger.debug("Отбрасываем уже пересланное событие Matrix %s", event_id)
            return
        label = _matrix_sender_display_name(room, sender_id)
        text = _format_relay_max_text(label, body)
        try:
            await self.max_gateway.send_text(bridge.max_chat_id, text)
            if event_id:
                await self.db.mark_relayed(event_id)
        except Exception as exc:
            logger.error("Сбой relay_matrix_to_max: %s", exc)

    async def relay_max_to_matrix(
        self,
        max_chat_id: int,
        from_user_id: int,
        label: str,
        body: str,
    ) -> None:
        # Команды, направленные сюда из обработчиков, но хер ли
        if classify(body)[0] != Intent.RELAY:
            return
        bridge = await self.db.get_bridge_by_max(max_chat_id)
        if not bridge:
            logger.warning("Нет сопоставления моста для чата Max chat_id=%s", max_chat_id)
            return
        content = _format_relay_matrix_content(label, body)
        try:
            await self.matrix_gateway.send_message(bridge.matrix_room_id, content)
            logger.debug(
                "Переслано Max->Matrix chat_id=%s room_id=%s",
                max_chat_id,
                bridge.matrix_room_id,
            )
        except Exception as exc:
            logger.error(
                "Сбой relay_max_to_matrix chat_id=%s room_id=%s: %s",
                max_chat_id,
                bridge.matrix_room_id,
                exc,
            )

    async def relay_max_media(self, message) -> None:
        if message.sender is None or message.recipient.chat_id is None:
            return
        bridge = await self.db.get_bridge_by_max(message.recipient.chat_id)
        if not bridge:
            return
        label = message.sender.username or message.sender.full_name or str(message.sender.user_id)
        ok = await self.media.relay_max_message_media(bridge.matrix_room_id, label, message)
        if not ok:
            try:
                await self.matrix_gateway.send_text(
                    bridge.matrix_room_id, self.strings.media_relay_failed
                )
            except Exception:
                logger.exception("relay_max_media: не удалось отправить сообщение об ошибке")

    async def relay_matrix_media_to_max(self, room, event, matrix_msgtype: str) -> None:
        room_id = room.room_id
        if not self.is_fresh_matrix_event(getattr(event, "server_timestamp", None)):
            return
        bridge = await self.db.get_bridge_by_matrix(room_id)
        if not bridge:
            return
        if event.sender == self.matrix_gateway.user_id:
            return
        label = _matrix_sender_display_name(room, event.sender)
        body = getattr(event, "body", None) or self.strings.attachment_label
        caption = _format_relay_max_text(label, body)
        ok = await self.media.relay_matrix_media_event_to_max(
            bridge.max_chat_id, caption, event, matrix_msgtype
        )
        if not ok:
            try:
                await self.matrix_gateway.send_text(room_id, self.strings.media_relay_failed_to_max)
            except Exception:
                logger.exception(
                    "relay_matrix_media_to_max: не удалось отправить сообщение об ошибке"
                )

    async def relay_matrix_sticker_to_max(self, room, event) -> None:
        room_id = room.room_id
        if not self.is_fresh_matrix_event(getattr(event, "server_timestamp", None)):
            return
        bridge = await self.db.get_bridge_by_matrix(room_id)
        if not bridge:
            return
        if event.sender == self.matrix_gateway.user_id:
            return
        content = event.source.get("content", {})
        mxc_url = content.get("url", "")
        if not mxc_url or not str(mxc_url).startswith("mxc://"):
            logger.warning("У стикера Matrix нет корректного mxc url")
            return
        body = content.get("body", "") or "sticker"
        label = _matrix_sender_display_name(room, event.sender)
        caption = _format_relay_max_text(label, body)
        await self.media.send_matrix_sticker_to_max(bridge.max_chat_id, caption, mxc_url, body)
