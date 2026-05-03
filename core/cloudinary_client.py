"""core/cloudinary_client.py — общий клиент для signed-upload в Cloudinary.

Используется и в miniapp (фото расклада), и в боте (фото клиента).
Конфиг берётся из env в одном из двух форматов:
- `CLOUDINARY_URL=cloudinary://<api_key>:<api_secret>@<cloud_name>` (приоритет);
- три отдельных ключа: `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`,
  `CLOUDINARY_API_SECRET` (Cloudinary-стандарт).
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Optional

import httpx

logger = logging.getLogger("core.cloudinary")

DEFAULT_FOLDER = "arcana-misc"

_log_once_state: dict[str, bool] = {"logged": False}


def _config() -> Optional[tuple[str, str, str]]:
    """→ (cloud_name, api_key, api_secret) или None.

    Один раз за процесс логирует источник конфига (без значений).
    """
    cu = os.environ.get("CLOUDINARY_URL", "").strip()
    if cu.startswith("cloudinary://"):
        try:
            rest = cu[len("cloudinary://"):]
            creds, cloud_name = rest.split("@", 1)
            api_key, api_secret = creds.split(":", 1)
            if cloud_name and api_key and api_secret:
                if not _log_once_state["logged"]:
                    logger.info("cloudinary configured via CLOUDINARY_URL")
                    _log_once_state["logged"] = True
                return cloud_name, api_key, api_secret
        except ValueError:
            logger.warning("CLOUDINARY_URL parse failed")

    cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME", "").strip()
    api_key = os.environ.get("CLOUDINARY_API_KEY", "").strip()
    api_secret = os.environ.get("CLOUDINARY_API_SECRET", "").strip()
    if cloud_name and api_key and api_secret:
        if not _log_once_state["logged"]:
            logger.info("cloudinary configured via separate keys")
            _log_once_state["logged"] = True
        return cloud_name, api_key, api_secret

    if not _log_once_state["logged"]:
        logger.info("cloudinary not configured")
        _log_once_state["logged"] = True
    return None


async def cloudinary_upload(
    file_bytes: bytes,
    filename: str = "upload.jpg",
    folder: str = DEFAULT_FOLDER,
) -> Optional[str]:
    """Загрузка байтов в Cloudinary. Возвращает secure_url или None.

    Возвращает None если конфиг не задан или Cloudinary вернул ошибку —
    вызывающий код сам решает, что делать.
    """
    cfg = _config()
    if not cfg:
        return None
    cloud_name, api_key, api_secret = cfg

    timestamp = str(int(time.time()))
    params = {"folder": folder, "timestamp": timestamp}
    to_sign = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    signature = hashlib.sha1((to_sign + api_secret).encode()).hexdigest()

    url = f"https://api.cloudinary.com/v1_1/{cloud_name}/image/upload"
    data = {
        "api_key": api_key,
        "timestamp": timestamp,
        "folder": folder,
        "signature": signature,
    }
    files = {"file": (filename, file_bytes)}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, data=data, files=files)
            if r.status_code >= 300:
                logger.warning("cloudinary upload %s: %s", r.status_code, r.text[:200])
                return None
            return r.json().get("secure_url")
    except Exception as e:
        logger.warning("cloudinary upload exception: %s", e)
        return None
