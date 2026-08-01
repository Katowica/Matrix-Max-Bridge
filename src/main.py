from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys

from maxapi import Bot, Dispatcher
from nio import AsyncClient

from src.bridge_service import BridgeService
from src.config import MATRIX_NIO_DEVICE_ID, Settings
from src.db import Database
from src.matrix_handlers import register_matrix_callbacks
from src.max_handlers import register_max_handlers
from src.nio_patch import apply_nio_schema_patches
from src.strings import make_strings

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )


class _ShutdownCoordinator:
    """Преобразует сигналы ОС в единое событие завершения asyncio."""

    def __init__(self) -> None:
        self.event = asyncio.Event()

    def trigger(self, *_args: object) -> None:
        if not self.event.is_set():
            logger.info("Получен сигнал завершения, корректно останавливаемся…")
            self.event.set()

    def install(self, loop: asyncio.AbstractEventLoop) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.trigger)
            except NotImplementedError, RuntimeError:
                # add_signal_handler недоступен на некоторых платформах (например,
                # Windows ProactorEventLoop или при запуске не из главного потока).
                # В этом случае возвращаемся к обработке KeyboardInterrupt по умолчанию.
                signal.signal(sig, lambda *_: self.trigger())


async def async_main() -> None:
    apply_nio_schema_patches()

    settings = Settings()
    db = await Database.connect(settings.database_path)
    await db.cleanup_expired_pending()
    if hasattr(db, "cleanup_relayed_events"):
        await db.cleanup_relayed_events()

    max_bot = Bot(
        settings.max_bot_token,
        after_upload_attempts=15,
        after_upload_retry_delay=4.0,
        after_upload_give_up_timeout=120.0,
    )
    matrix = AsyncClient(
        settings.matrix_homeserver_base(),
        user=settings.matrix_user_id,
        device_id=MATRIX_NIO_DEVICE_ID,
        store_path="",
    )
    # restore_login обязателен: при создании только с access_token в nio
    # поле user_id остаётся пустым.
    matrix.restore_login(
        settings.matrix_user_id,
        MATRIX_NIO_DEVICE_ID,
        settings.matrix_access_token,
    )

    strings = make_strings(settings.matrix_user_id)

    bridge = BridgeService(settings, db, max_bot, matrix, strings)
    register_matrix_callbacks(matrix, bridge)

    me = await max_bot.get_me()

    dp = Dispatcher()
    register_max_handlers(dp, bridge, me.user_id)

    shutdown = _ShutdownCoordinator()
    shutdown.install(asyncio.get_running_loop())

    log = logging.getLogger(__name__)
    log.info("Запуск синхронизации Matrix и опроса Max")

    sync_task = asyncio.create_task(matrix.sync_forever(timeout=30000, full_state=True))
    # skip_updates по умолчанию false иначе голову оторвут,
    # пришедшие во время простоя.
    poll_task = asyncio.create_task(dp.start_polling(max_bot))

    stop_watch = asyncio.create_task(shutdown.event.wait())
    try:
        await asyncio.wait(
            {stop_watch, sync_task, poll_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        for t in (sync_task, poll_task):
            t.cancel()
        for t in (sync_task, poll_task):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        stop_watch.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await stop_watch
        await max_bot.close_session()
        await matrix.close()
        await db.close()


def main() -> None:
    _configure_logging()
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
