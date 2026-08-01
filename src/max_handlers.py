from __future__ import annotations

import logging

from maxapi import Dispatcher, F
from maxapi.types.updates.bot_added import BotAdded
from maxapi.types.updates.message_created import MessageCreated

from src.bridge_service import BridgeService

logger = logging.getLogger(__name__)


def register_max_handlers(dp: Dispatcher, bridge: BridgeService, own_user_id: int) -> None:
    @dp.bot_added()
    async def on_bot_added(event: BotAdded) -> None:
        if event.is_channel:
            return
        await bridge.issue_link_for_max_chat(event.chat_id)

    @dp.message_created(F.message.body.text)
    async def on_text(event: MessageCreated) -> None:
        message = event.message
        if message.sender is None or message.sender.user_id == own_user_id:
            return
        chat_id = message.recipient.chat_id
        if chat_id is None:
            return
        text = message.body.text or "" if message.body else ""
        if bridge.is_link_request_command(text):
            await bridge.issue_link_for_max_chat(chat_id)
            return
        if bridge.is_unlink_command(text):
            await bridge.unlink_from_max(chat_id, message.sender.user_id)
            return
        label = message.sender.username or message.sender.full_name or str(message.sender.user_id)
        await bridge.relay_max_to_matrix(chat_id, message.sender.user_id, label, text)

    @dp.message_created(F.message.body.attachments)
    async def on_media(event: MessageCreated) -> None:
        message = event.message
        if message.sender is None or message.sender.user_id == own_user_id:
            return
        await bridge.relay_max_media(message)
