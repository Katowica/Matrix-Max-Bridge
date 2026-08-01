from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from maxapi import Bot
from nio import AsyncClient, RoomGetStateEventResponse

from src.media_relay import (
    download_mxc_to_bytes,
    relay_matrix_media_event_to_max,
    relay_max_message_media,
    send_matrix_sticker_to_max,
)
from src.strings import Strings

if TYPE_CHECKING:
    from maxapi.types.message import Message

logger = logging.getLogger(__name__)


class MatrixGateway:
    def __init__(self, client: AsyncClient) -> None:
        self.client = client

    @property
    def user_id(self) -> str:
        return self.client.user_id

    @property
    def rooms(self) -> dict:
        return self.client.rooms

    async def send_text(self, room_id: str, text: str) -> None:
        await self.client.room_send(room_id, "m.room.message", {"msgtype": "m.text", "body": text})

    async def send_message(self, room_id: str, content: dict) -> None:
        await self.client.room_send(room_id, "m.room.message", content)

    async def is_room_encrypted(self, room_id: str) -> bool:
        resp = await self.client.room_get_state_event(room_id, "m.room.encryption", "")
        return isinstance(resp, RoomGetStateEventResponse)

    async def download_mxc(self, mxc_url: str, *, size_limit: int):
        return await download_mxc_to_bytes(self.client, mxc_url, size_limit=size_limit)


class MaxGateway:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def send_text(self, chat_id: int, text: str) -> None:
        await self.bot.send_message(chat_id=chat_id, text=text)

    async def get_chat_member(self, max_chat_id: int, user_id: int):
        return await self.bot.get_chat_member(max_chat_id, user_id)


class MediaRelayService:
    def __init__(
        self,
        max_gateway: MaxGateway,
        matrix_gateway: MatrixGateway,
        strings: Strings,
        size_limit: int,
    ) -> None:
        self.max_gateway = max_gateway
        self.matrix_gateway = matrix_gateway
        self.strings = strings
        self.size_limit = size_limit

    async def relay_max_message_media(self, room_id: str, label: str, message: Message) -> bool:
        return await relay_max_message_media(
            self.max_gateway.bot,
            self.matrix_gateway.client,
            room_id,
            label,
            message,
            size_limit=self.size_limit,
        )

    async def relay_matrix_media_event_to_max(
        self, chat_id: int, caption: str, event, matrix_msgtype: str
    ) -> bool:
        return await relay_matrix_media_event_to_max(
            self.max_gateway.bot,
            self.matrix_gateway.client,
            chat_id,
            caption,
            event,
            matrix_msgtype,
            size_limit=self.size_limit,
        )

    async def send_matrix_sticker_to_max(
        self, chat_id: int, caption: str, mxc_url: str, body: str
    ) -> bool:
        from src.media_relay import guess_ext

        dl = await self.matrix_gateway.download_mxc(mxc_url, size_limit=self.size_limit)
        if not dl:
            return False
        data, mime, fname = dl
        filename = fname or f"sticker{guess_ext(mime, '.webp')}"
        try:
            await send_matrix_sticker_to_max(
                self.max_gateway.bot, chat_id, caption, data, filename, mime
            )
            return True
        except Exception:
            logger.exception("Не удалось отправить стикер Matrix в Max")
            return False
