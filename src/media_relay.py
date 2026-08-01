from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
from urllib.parse import quote

import aiohttp
from maxapi import Bot
from maxapi.enums.upload_type import UploadType
from maxapi.types.attachments.audio import Audio
from maxapi.types.attachments.file import File
from maxapi.types.attachments.image import Image
from maxapi.types.attachments.sticker import Sticker
from maxapi.types.attachments.video import Video
from maxapi.types.input_media import InputMediaBuffer
from maxapi.types.message import Message
from nio import AsyncClient, DownloadError, MemoryDownloadResponse, UploadError, UploadResponse

logger = logging.getLogger(__name__)

_DOWNLOAD_SEMAPHORE = asyncio.Semaphore(4)


def _truncate_caption(s: str, max_len: int = 1024) -> str:
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def guess_ext(mime: str, fallback: str) -> str:
    ext = mimetypes.guess_extension(mime or "") or ""
    if ext in (".htm", ".html", ".php"):
        ext = ""
    return ext or fallback


def _too_large(size: int, limit: int) -> bool:
    return size > limit


# Сигнатуры magic-байтов для распространённых растровых форматов изображений
_IMAGE_SIGNATURES: tuple[bytes, ...] = (
    b"\xff\xd8\xff",  # JPEG
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"GIF87a",  # GIF
    b"GIF89a",
    b"RIFF",  # WebP (RIFF....WEBP)
    b"\x42\x4d",  # BMP
)


def _looks_like_image(data: bytes) -> bool:
    return any(data.startswith(sig) for sig in _IMAGE_SIGNATURES)


async def upload_bytes_to_matrix(
    client: AsyncClient,
    data: bytes,
    content_type: str,
    filename: str | None,
    *,
    size_limit: int,
) -> str | None:
    if _too_large(len(data), size_limit):
        logger.warning("Пропуск загрузки в Matrix: файл превышает %d байт", size_limit)
        return None

    def provider(_got_429: int, _got_timeouts: int):
        import io

        return io.BytesIO(data)

    resp, _ = await client.upload(
        provider,
        content_type=content_type or "application/octet-stream",
        filename=filename,
        filesize=len(data),
    )
    if isinstance(resp, UploadError):
        logger.error("Сбой загрузки в Matrix: %s", resp.message)
        return None
    if isinstance(resp, UploadResponse):
        return resp.content_uri
    return None


async def download_mxc_to_bytes(
    client: AsyncClient,
    mxc_url: str,
    *,
    size_limit: int,
) -> tuple[bytes, str, str | None] | None:
    async with _DOWNLOAD_SEMAPHORE:
        authenticated = await _download_mxc_authenticated(client, mxc_url, size_limit)
        if authenticated is not None:
            return authenticated

        # Запасной вариант для старых homeserver'ов / конфигураций с legacy media API.
        result = await client.download(mxc_url)
        if isinstance(result, DownloadError):
            logger.error("Сбой скачивания из Matrix (legacy-эндпоинт): %s", result.message)
            return None
        if isinstance(result, MemoryDownloadResponse):
            if _too_large(len(result.body), size_limit):
                logger.warning(
                    "Скачивание медиа из Matrix (legacy) превысило лимит размера, пропуск"
                )
                return None
            return result.body, result.content_type or "application/octet-stream", result.filename
        return None


def _parse_mxc_uri(mxc_url: str) -> tuple[str, str] | None:
    if not mxc_url.startswith("mxc://"):
        return None
    rest = mxc_url[len("mxc://") :]
    if "/" not in rest:
        return None
    server_name, media_id = rest.split("/", 1)
    if not server_name or not media_id:
        return None
    return server_name, media_id


def _filename_from_content_disposition(header_value: str | None) -> str | None:
    if not header_value:
        return None
    parts = [p.strip() for p in header_value.split(";")]
    for p in parts:
        if p.lower().startswith("filename*="):
            raw = p.split("=", 1)[1].strip().strip('"')
            if "''" in raw:
                raw = raw.split("''", 1)[1]
            return raw
        if p.lower().startswith("filename="):
            return p.split("=", 1)[1].strip().strip('"')
    return None


async def _download_mxc_authenticated(
    client: AsyncClient,
    mxc_url: str,
    size_limit: int,
) -> tuple[bytes, str, str | None] | None:
    parsed = _parse_mxc_uri(mxc_url)
    if not parsed:
        logger.warning("Некорректный mxc url: %s", mxc_url)
        return None
    if not client.access_token:
        logger.warning("Нет токена доступа Matrix для авторизованного скачивания медиа")
        return None
    server_name, media_id = parsed
    base = client.homeserver.rstrip("/")
    url = (
        f"{base}/_matrix/client/v1/media/download/"
        f"{quote(server_name, safe='[]:.')}/{quote(media_id, safe='')}"
    )
    headers = {"Authorization": f"Bearer {client.access_token}"}
    timeout = aiohttp.ClientTimeout(total=60)
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get(url, headers=headers) as resp,
        ):
            if resp.status == 200:
                content_length = resp.headers.get("Content-Length")
                if content_length and int(content_length) > size_limit:
                    logger.warning(
                        "Медиа из Matrix слишком велико для скачивания: Content-Length=%s лимит=%s",
                        content_length,
                        size_limit,
                    )
                    return None
                body = await resp.read()
                if _too_large(len(body), size_limit):
                    logger.warning("Скачивание медиа из Matrix превысило лимит размера, пропуск")
                    return None
                ctype = resp.headers.get("Content-Type", "application/octet-stream")
                disposition = resp.headers.get("Content-Disposition")
                filename = _filename_from_content_disposition(disposition)
                if ctype.startswith("image/") and not _looks_like_image(body):
                    logger.warning(
                        "Скачанное изображение из Matrix выглядит некорректным: mime=%s размер=%d",
                        ctype,
                        len(body),
                    )
                    return None
                return body, ctype, filename
            if resp.status != 404:
                text = await resp.text()
                logger.warning(
                    "Сбой авторизованного скачивания медиа из Matrix status=%s (тело опущено)",
                    resp.status,
                )
                # Не логируем тела ответов сервера — могут содержать идентификаторы.
                logger.debug("Длина превью тела ответа (не 200)=%d", len(text))
                return None
            logger.info(
                "Авторизованный media-эндпоинт Matrix вернул 404, откатываемся к legacy: %s",
                mxc_url,
            )
            return None
    except Exception:
        logger.exception("Исключение при авторизованном скачивании медиа из Matrix для %s", mxc_url)
        return None


async def send_media_to_matrix(
    client: AsyncClient,
    room_id: str,
    msgtype: str,
    body: str,
    data: bytes,
    mime: str,
    filename: str,
    *,
    size_limit: int,
    info_extras: dict | None = None,
) -> bool:
    uri = await upload_bytes_to_matrix(client, data, mime, filename, size_limit=size_limit)
    if not uri:
        return False
    info: dict = {"mimetype": mime, "size": len(data)}
    if info_extras:
        info.update(info_extras)
    content: dict = {"msgtype": msgtype, "body": body, "url": uri, "info": info}
    if msgtype == "m.file":
        content["filename"] = filename
    await client.room_send(room_id, "m.room.message", content)
    return True


async def send_matrix_media_to_max(
    bot: Bot,
    chat_id: int,
    caption: str,
    data: bytes,
    filename: str,
    mime: str,
    matrix_msgtype: str,
) -> None:
    cap = _truncate_caption(caption)
    fn0 = filename or "file"

    if matrix_msgtype == "m.image":
        upload_type = UploadType.IMAGE
    elif matrix_msgtype == "m.video":
        upload_type = UploadType.VIDEO
    elif matrix_msgtype == "m.audio":
        upload_type = UploadType.AUDIO
    else:
        upload_type = UploadType.FILE

    media = InputMediaBuffer(buffer=data, filename=fn0, type=upload_type)
    try:
        await bot.send_message(chat_id=chat_id, text=cap, attachments=[media])
    except Exception:
        logger.exception(
            "Сбой send_matrix_media_to_max: msgtype=%s upload_type=%s mime=%s размер=%d",
            matrix_msgtype,
            upload_type,
            mime,
            len(data),
        )
        raise


def _compose_media_body(label: str, kind: str, caption: str) -> str:
    prefix = f"{label}: [{kind}]"
    if caption:
        return f"{prefix} {caption}"
    return prefix


def _attachment_url(attachment) -> str | None:
    payload = getattr(attachment, "payload", None)
    url = getattr(payload, "url", None)
    if isinstance(url, str) and url:
        return url
    if isinstance(attachment, Video) and attachment.urls is not None:
        for attr in ("mp4_720", "mp4_480", "mp4_360", "mp4_240", "mp4_144", "mp4_1080", "hls"):
            video_url = getattr(attachment.urls, attr, None)
            if video_url:
                return video_url
    return None


async def _download_max_attachment(bot: Bot, url: str, size_limit: int) -> bytes | None:
    async with _DOWNLOAD_SEMAPHORE:
        try:
            data = await bot.download_bytes(url)
        except Exception:
            logger.exception("Сбой скачивания вложения Max")
            return None
    if not data:
        return None
    if _too_large(len(data), size_limit):
        logger.warning(
            "Вложение Max превышает лимит размера (%d > %d), пропуск", len(data), size_limit
        )
        return None
    return data


async def relay_max_message_media(
    bot: Bot,
    matrix: AsyncClient,
    room_id: str,
    label: str,
    message: Message,
    *,
    size_limit: int,
) -> bool:
    body = message.body
    if body is None or not body.attachments:
        return False
    caption = (body.text or "").strip()
    attachment = body.attachments[0]

    url = _attachment_url(attachment)
    if not url:
        logger.warning("У вложения Max нет скачиваемого URL")
        return False

    data = await _download_max_attachment(bot, url, size_limit)
    if not data:
        return False

    composed = lambda kind: _compose_media_body(label, kind, caption)  # noqa: E731

    if isinstance(attachment, Image):
        mime = "image/jpeg"
        fn = f"photo{guess_ext(mime, '.jpg')}"
        return await send_media_to_matrix(
            matrix,
            room_id,
            "m.image",
            composed("photo"),
            data,
            mime,
            fn,
            size_limit=size_limit,
        )

    if isinstance(attachment, Video):
        mime = "video/mp4"
        fn = f"video{guess_ext(mime, '.mp4')}"
        return await send_media_to_matrix(
            matrix,
            room_id,
            "m.video",
            composed("video"),
            data,
            mime,
            fn,
            size_limit=size_limit,
            info_extras={"duration": int(attachment.duration)} if attachment.duration else None,
        )

    if isinstance(attachment, Audio):
        mime = "audio/ogg"
        fn = f"voice{guess_ext(mime, '.ogg')}"
        return await send_media_to_matrix(
            matrix,
            room_id,
            "m.audio",
            composed("voice"),
            data,
            mime,
            fn,
            size_limit=size_limit,
            info_extras={"org.matrix.msc3245.voice": True},
        )

    if isinstance(attachment, File):
        mime = "application/octet-stream"
        ext = guess_ext(mime, "")
        fn = attachment.filename or (f"file{ext}" if ext else "file.bin")
        return await send_media_to_matrix(
            matrix,
            room_id,
            "m.file",
            composed("file"),
            data,
            mime,
            fn,
            size_limit=size_limit,
        )

    if isinstance(attachment, Sticker):
        mime = "image/webp"
        fn = "sticker.webp"
        return await send_media_to_matrix(
            matrix,
            room_id,
            "m.image",
            composed("sticker"),
            data,
            mime,
            fn,
            size_limit=size_limit,
        )

    return False


async def relay_matrix_media_event_to_max(
    bot: Bot,
    matrix: AsyncClient,
    chat_id: int,
    caption: str,
    event,
    matrix_msgtype: str,
    *,
    size_limit: int,
) -> bool:
    url = getattr(event, "url", None)
    if not url or not str(url).startswith("mxc://"):
        logger.warning("Пропуск медиа из Matrix: нет mxc url (%s)", matrix_msgtype)
        return False
    dl = await download_mxc_to_bytes(matrix, url, size_limit=size_limit)
    if not dl:
        return False
    data, mime, fname = dl
    # Только агрегированные метаданные — никогда не логируем сырые байты контента (H-6).
    logger.debug("Медиа из Matrix скачано: mime=%s размер=%d", mime, len(data))
    body = getattr(event, "body", "") or ""
    filename = fname or os.path.basename(body) or "file"
    if "." not in filename and mime:
        filename += guess_ext(mime, ".bin")
    try:
        await send_matrix_media_to_max(bot, chat_id, caption, data, filename, mime, matrix_msgtype)
    except Exception:
        logger.exception("Не удалось отправить медиа в Max")
        return False
    return True


async def send_matrix_sticker_to_max(
    bot: Bot,
    chat_id: int,
    caption: str,
    data: bytes,
    filename: str,
    mime: str,
) -> None:
    fn = filename or "sticker.webp"
    upload_type = UploadType.IMAGE if mime == "image/webp" else UploadType.FILE
    media = InputMediaBuffer(buffer=data, filename=fn, type=upload_type)
    cap = _truncate_caption(caption)
    await bot.send_message(chat_id=chat_id, text=cap, attachments=[media])
