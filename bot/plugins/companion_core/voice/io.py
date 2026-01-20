"""语音 IO（OneBot/NapCat）。

职责：
- 从 OneBot v11 的 record segment 取到真实音频（二进制）
- 兼容 url/base64/file 等常见来源，统一落到本地临时文件
"""

from __future__ import annotations

import base64
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx
from nonebot import logger


def _is_probably_url(s: str) -> bool:
    try:
        u = urlparse(s)
        return u.scheme in ("http", "https")
    except Exception:
        return False


def _strip_file_scheme(p: str) -> str:
    if p.startswith("file://"):
        return p[7:]
    return p


async def materialize_onebot_file(
    *,
    file_value: str,
    suffix: str = ".dat",
    proxy: str | None = None,
) -> Path:
    """
    把 OneBot 的 file 字段（可能是 url / base64:// / file://）落到本地临时文件，返回路径。

    注意：如果返回的是 NapCat/OneBot 服务端容器内的本地路径，本容器未必能读到；
    这种情况下需要 OneBot 侧提供 url 或 base64 才能真正取到数据。
    """
    v = (file_value or "").strip()
    if not v:
        raise ValueError("empty file")

    td = tempfile.mkdtemp(prefix="qqbot_onebot_")
    out = Path(td) / f"onebot{suffix}"

    if v.startswith("base64://"):
        out.write_bytes(base64.b64decode(v[len("base64://") :]))
        return out

    if _is_probably_url(v):
        async with httpx.AsyncClient(timeout=30.0, proxy=proxy, trust_env=(proxy is None)) as client:
            r = await client.get(v)
            r.raise_for_status()
            out.write_bytes(r.content)
        return out

    # file:// or plain path
    local_path = Path(_strip_file_scheme(v)).expanduser()
    if local_path.exists():
        out.write_bytes(local_path.read_bytes())
        return out

    raise FileNotFoundError(f"Cannot access onebot file path: {v!r}")


async def fetch_record_from_event(bot, record_segment) -> Path:
    """
    从 OneBot v11 的 record segment 获取音频数据并保存到本地临时文件。

    优先使用 segment.data.url（若存在），否则尝试调用 get_record。
    """
    data = getattr(record_segment, "data", {}) or {}
    proxy = os.getenv("DASHSCOPE_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or None

    url = str(data.get("url") or "").strip()
    if url:
        return await materialize_onebot_file(file_value=url, suffix=".audio", proxy=proxy)

    file_id = str(data.get("file") or "").strip()
    if not file_id:
        raise RuntimeError("record segment missing data.file/url")

    try:
        resp = await bot.call_api("get_record", file=file_id, out_format="mp3")
    except Exception as e:
        logger.warning(f"[voice] get_record failed: {e}")
        raise

    if isinstance(resp, dict):
        d = resp.get("data", resp)
        file_val = str(d.get("file") or d.get("url") or "").strip()
        if file_val:
            return await materialize_onebot_file(file_value=file_val, suffix=".mp3", proxy=proxy)

    raise RuntimeError(f"Unexpected get_record response: {resp!r}")

