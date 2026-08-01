from __future__ import annotations

import logging

from nio import (
    AsyncClient,
    InviteMemberEvent,
    JoinError,
    JoinResponse,
    MatrixRoom,
    RoomMemberEvent,
    RoomMessageAudio,
    RoomMessageFile,
    RoomMessageImage,
    RoomMessageText,
    RoomMessageVideo,
    SyncResponse,
    UnknownEvent,
)

from src.bridge_service import BridgeService
from src.command_router import Intent, classify

logger = logging.getLogger(__name__)


def _own_mxid(client: AsyncClient) -> str:
    uid = (getattr(client, "user_id", "") or "").strip()
    if uid:
        return uid
    return (getattr(client, "user", "") or "").strip()


async def _join_room(client: AsyncClient, room_id: str) -> None:
    resp = await client.join(room_id)
    if isinstance(resp, JoinError):
        logger.error("Сбой присоединения к комнате Matrix %s: %s", room_id, resp.message)
    elif isinstance(resp, JoinResponse):
        logger.info("Присоединились к комнате Matrix %s", room_id)


def register_matrix_callbacks(client: AsyncClient, bridge: BridgeService) -> None:
    first_sync_done = False

    async def on_first_sync(_response: SyncResponse) -> None:
        nonlocal first_sync_done
        if first_sync_done:
            return
        first_sync_done = True
        for room_id in list(client.rooms.keys()):
            try:
                await bridge.on_bot_joined_matrix_room(room_id)
            except Exception:
                logger.exception("Сбой начальной отправки приветствий Matrix для %s", room_id)
        me = _own_mxid(client)
        for room_id in list(client.invited_rooms.keys()):
            if room_id in client.rooms:
                continue
            try:
                logger.info(
                    "Автоприсоединение к остаточному приглашению: %s (mxid=%s)", room_id, me
                )
                await _join_room(client, room_id)
            except Exception:
                logger.exception(
                    "Сбой присоединения (стартовое приглашение) Matrix для %s", room_id
                )

    async def on_invite(room: MatrixRoom, event: InviteMemberEvent) -> None:
        me = _own_mxid(client)
        if event.state_key != me:
            logger.debug(
                "Приглашение m.room.member для другого пользователя: state_key=%s мы=%s",
                event.state_key,
                me,
            )
            return
        room_id = room.room_id
        if not await bridge.room_server_allowed(room_id):
            logger.warning("Отклоняем приглашение с неразрешённого сервера: room_id=%s", room_id)
            return
        logger.info("Принимаем приглашение в комнату %s (mxid=%s)", room_id, me)
        await _join_room(client, room_id)

    async def on_room_member(room: MatrixRoom, event: RoomMemberEvent) -> None:
        if event.state_key != _own_mxid(client):
            return
        if event.membership != "join":
            return
        await bridge.on_bot_joined_matrix_room(room.room_id)

    async def on_text(room: MatrixRoom, event: RoomMessageText) -> None:
        body = event.body or ""
        intent, _ = classify(body)
        if intent in (Intent.LINK, Intent.UNLINK):
            await bridge.try_link_from_matrix(room, event.sender, body)
        elif intent == Intent.LINK_REQUEST:
            await bridge.send_matrix_link_instructions(room.room_id)
        else:
            await bridge.relay_matrix_to_max(
                room,
                event.sender,
                body,
                server_ts_ms=getattr(event, "server_timestamp", None),
                event_id=getattr(event, "event_id", None),
            )

    async def on_room_media(
        room: MatrixRoom,
        event: RoomMessageImage | RoomMessageVideo | RoomMessageAudio | RoomMessageFile,
    ) -> None:
        if isinstance(event, RoomMessageImage):
            mt = "m.image"
        elif isinstance(event, RoomMessageVideo):
            mt = "m.video"
        elif isinstance(event, RoomMessageAudio):
            mt = "m.audio"
        elif isinstance(event, RoomMessageFile):
            mt = "m.file"
        else:
            return
        await bridge.relay_matrix_media_to_max(room, event, mt)

    async def on_unknown_event(room: MatrixRoom, event: UnknownEvent) -> None:
        if getattr(event, "type", None) != "m.sticker":
            return
        await bridge.relay_matrix_sticker_to_max(room, event)

    client.add_response_callback(on_first_sync, SyncResponse)
    client.add_event_callback(on_invite, InviteMemberEvent)
    client.add_event_callback(on_room_member, RoomMemberEvent)
    client.add_event_callback(on_text, RoomMessageText)
    client.add_event_callback(
        on_room_media,
        (RoomMessageImage, RoomMessageVideo, RoomMessageAudio, RoomMessageFile),
    )
    client.add_event_callback(on_unknown_event, UnknownEvent)
