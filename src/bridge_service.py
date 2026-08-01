from __future__ import annotations

import logging
import time

from maxapi import Bot
from nio import AsyncClient

from src.command_router import Intent, classify
from src.config import Settings
from src.db import Database
from src.gateways import MatrixGateway, MaxGateway, MediaRelayService
from src.services import AuthorizationService, LinkingService, RelayService
from src.strings import Strings

logger = logging.getLogger(__name__)


class BridgeService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        max_bot: Bot,
        matrix: AsyncClient,
        strings: Strings,
    ) -> None:
        self.settings = settings
        self.matrix_gateway = MatrixGateway(matrix)
        self.max_gateway = MaxGateway(max_bot)
        self.auth = AuthorizationService(self.max_gateway)
        self.media = MediaRelayService(
            self.max_gateway, self.matrix_gateway, strings, settings.max_download_bytes
        )
        self.started_at_ms = int(time.time() * 1000)
        self.linking = LinkingService(
            db,
            self.matrix_gateway,
            self.max_gateway,
            self.auth,
            strings,
            settings,
        )
        self.relay = RelayService(
            db,
            self.matrix_gateway,
            self.max_gateway,
            self.media,
            strings,
            self.started_at_ms,
        )
        self.db = db
        self.strings = strings

    def is_fresh_matrix_event(self, server_ts_ms: int | None) -> bool:
        return self.relay.is_fresh_matrix_event(server_ts_ms)

    def parse_link_command(self, body: str) -> str | None:
        intent, code = classify(body)
        return code if intent == Intent.LINK else None

    def is_unlink_command(self, body: str) -> bool:
        return classify(body)[0] == Intent.UNLINK

    def is_link_request_command(self, body: str) -> bool:
        return classify(body)[0] == Intent.LINK_REQUEST

    async def room_server_allowed(self, room_id: str) -> bool:
        from src.services import HostnamePolicy

        return await HostnamePolicy.room_server_allowed(
            self.matrix_gateway, self.settings.matrix_allowed_server, room_id
        )

    async def is_room_encrypted(self, room_id: str) -> bool:
        return await self.matrix_gateway.is_room_encrypted(room_id)

    async def send_matrix_plain(self, room_id: str, text: str) -> None:
        await self.matrix_gateway.send_text(room_id, text)

    async def send_matrix_link_instructions(self, room_id: str) -> None:
        await self.matrix_gateway.send_text(room_id, self.strings.matrix_welcome)

    async def on_bot_joined_matrix_room(self, room_id: str) -> None:
        await self.linking.on_bot_joined_matrix_room(room_id)

    async def maybe_send_matrix_welcome(self, room_id: str) -> None:
        await self.linking.maybe_send_matrix_welcome(room_id)

    async def issue_link_for_max_chat(self, max_chat_id: int) -> None:
        await self.linking.issue_link_for_max_chat(max_chat_id, started_at_ms=self.started_at_ms)

    async def try_link_from_matrix(self, room, sender_id: str, raw_body: str) -> None:
        await self.linking.try_link_from_matrix(room, sender_id, raw_body)

    async def unlink_from_matrix(self, room, sender_id: str) -> None:
        await self.linking.unlink_from_matrix(room, sender_id)

    async def unlink_from_max(self, max_chat_id: int, from_user_id: int) -> None:
        await self.linking.unlink_from_max(max_chat_id, from_user_id)

    async def relay_matrix_to_max(
        self,
        room,
        sender_id: str,
        body: str,
        *,
        server_ts_ms: int | None = None,
        event_id: str | None = None,
    ) -> None:
        await self.relay.relay_matrix_to_max(
            room, sender_id, body, server_ts_ms=server_ts_ms, event_id=event_id
        )

    async def relay_max_to_matrix(
        self, max_chat_id: int, from_user_id: int, label: str, body: str
    ) -> None:
        await self.relay.relay_max_to_matrix(max_chat_id, from_user_id, label, body)

    async def relay_max_media(self, message) -> None:
        await self.relay.relay_max_media(message)

    async def relay_matrix_media_to_max(self, room, event, matrix_msgtype: str) -> None:
        await self.relay.relay_matrix_media_to_max(room, event, matrix_msgtype)

    async def relay_matrix_sticker_to_max(self, room, event) -> None:
        await self.relay.relay_matrix_sticker_to_max(room, event)
