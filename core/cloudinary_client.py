"""core/cloudinary_client.py — общий клиент для signed-upload в Cloudinary.

Используется и в miniapp (фото расклада), и в боте (фото клиента).
Требует CLOUDINARY_URL = cloudinary://<api_key>:<api_secret>@<cloud_name>.
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


async def cloudinary_upload(
    file_bytes: bytes,
    filename: str = "upload.jpg",
    folder: str = DEFAULT_FOLDER,
) -> Optional[str]:
    """Загрузка байтов в Cloudinary. Возвращает secure_url или None.

    Возвращает None если CLOUDINARY_URL не настроен или Cloudinary вернул
    ошибку — вызывающий код сам решает, что делать.
    """
    cu = os.environ.get("CLOUDINARY_URL", "")
    if not cu.startswith("cloudinary://"):
        return None
    try:
        rest = cu[len("cloudinary://"):]
        creds, cloud_name = rest.split("@", 1)
        api_key, api_secret = creds.split(":", 1)
    except ValueError:
        logger.warning("CLOUDINARY_URL parse failed")
        return None

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
