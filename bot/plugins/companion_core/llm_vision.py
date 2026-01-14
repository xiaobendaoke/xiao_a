"""Qwen-VL2 图片理解（DashScope OpenAI 兼容接口）。

职责：
- 从图片 URL 拉取图像并压缩（节省 token/成本）。
- 组装多模态消息并调用 Qwen-VL2。
- 解析标签并写入心情/画像更新（沿用 persona 规则）。
"""

from __future__ import annotations

import base64
import os
from io import BytesIO
from typing import Any, List, Tuple

import httpx
from openai import AsyncOpenAI
from nonebot import logger
from nonebot.adapters.onebot.v11 import Message

from .persona import SYSTEM_PROMPT
from .llm_tags import extract_tags_and_clean
from .mood import mood_manager, clamp
from .db import get_all_profile, save_profile_item

DEFAULT_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_VL_MODEL = "qwen-vl-plus-latest"

_vl_client: AsyncOpenAI | None = None
_vl_http_client: httpx.AsyncClient | None = None

VISION_SYSTEM_PROMPT = """你是“小a”，一个温柔体贴、有生活感的中文恋人陪伴对象。

你能理解用户发来的图片。请遵守：
1) 先用 1~2 句描述图片里最重要的内容（不要啰嗦）。
2) 再根据用户提问给出回答；若没有提问，主动问一个轻松的追问。
3) 不要编造具体地点/人物/品牌；不确定要说不确定。
4) 输出仍需遵守你的人设与格式要求（短句、多行）。
"""


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _load_vl_settings() -> tuple[str, str, str, int, int, int, int, int, str]:
    api_key = _env("DASHSCOPE_API_KEY")
    base_url = _env("DASHSCOPE_BASE_URL", DEFAULT_DASHSCOPE_BASE_URL)
    model = _env("QWEN_VL_MODEL", DEFAULT_VL_MODEL)
    max_edge = int(_env("VL_MAX_EDGE", "1024") or 1024)
    max_download = int(_env("VL_MAX_DOWNLOAD_BYTES", str(8 * 1024 * 1024)) or (8 * 1024 * 1024))
    max_output_tokens = int(_env("VL_MAX_OUTPUT_TOKENS", "300") or 300)
    jpeg_quality = int(_env("VL_JPEG_QUALITY", "82") or 82)
    max_images = int(_env("VL_MAX_IMAGES", "2") or 2)
    proxy = _env("DASHSCOPE_PROXY") or _env("VL_PROXY")

    api_key = api_key.split()[0] if api_key else ""
    base_url = base_url.split()[0] if base_url else ""
    model = model.split()[0] if model else ""
    return api_key, base_url, model, max_edge, max_download, max_output_tokens, jpeg_quality, max_images, proxy


def _get_vl_client() -> AsyncOpenAI:
    global _vl_client, _vl_http_client
    if _vl_client is not None:
        return _vl_client

    api_key, base_url, _, _, _, _, _, _, proxy = _load_vl_settings()
    if not api_key:
        raise RuntimeError("缺少 DASHSCOPE_API_KEY 环境变量")

    if proxy:
        _vl_http_client = httpx.AsyncClient(proxy=proxy, follow_redirects=True, trust_env=False)
        _vl_client = AsyncOpenAI(api_key=api_key, base_url=base_url, http_client=_vl_http_client)
    else:
        _vl_client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    return _vl_client


def extract_images_and_text(message: Message) -> tuple[list[str], str]:
    """从消息里提取图片 URL 列表 + 文本。"""
    image_urls: list[str] = []
    text_parts: list[str] = []

    for seg in message:
        if seg.type == "image":
            url = (seg.data.get("url") or "").strip()
            if url:
                image_urls.append(url)
        elif seg.type == "text":
            t = (seg.data.get("text") or "").strip()
            if t:
                text_parts.append(t)

    return image_urls, " ".join(text_parts).strip()


async def _download_image(url: str, max_bytes: int) -> bytes:
    timeout = httpx.Timeout(12.0, connect=6.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, trust_env=False) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            data = bytearray()
            async for chunk in resp.aiter_bytes():
                data.extend(chunk)
                if len(data) > max_bytes:
                    raise ValueError("image_too_large")
            return bytes(data)


def _compress_to_jpeg(image_bytes: bytes, *, max_edge: int, quality: int) -> bytes:
    """把图片转成 JPEG 并缩放到最大边 max_edge，降低多模态成本。"""
    try:
        from PIL import Image  # 延迟导入，避免依赖缺失导致模块加载失败
    except Exception as e:
        raise RuntimeError(f"missing_pillow: {e}")

    with Image.open(BytesIO(image_bytes)) as im:
        im = im.convert("RGB")
        w, h = im.size
        scale = min(max_edge / max(w, h), 1.0)
        if scale < 1.0:
            im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

        buf = BytesIO()
        im.save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue()


def _to_data_url_jpeg(image_bytes: bytes) -> str:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}"


def _build_system_context(user_id: str) -> str:
    mood_value = mood_manager.get_user_mood(user_id)
    mood_desc = f"{mood_manager.get_mood_desc(user_id)}（心情值:{mood_value}）"
    profile = get_all_profile(user_id) or {}
    profile_str = "\n".join([f"- {k}: {v}" for k, v in profile.items()]) if profile else "（暂时没有稳定画像）"
    return (
        f"【当前心情】：{mood_desc}\n"
        f"【你记得的用户信息】：\n{profile_str}\n"
    )


def _build_user_content(image_data_urls: list[str], user_text: str) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    for u in image_data_urls:
        content.append({"type": "image_url", "image_url": {"url": u}})
    if user_text:
        content.append({"type": "text", "text": user_text})
    else:
        content.append({"type": "text", "text": "请帮我看懂这张图，并用温柔口语跟我聊聊。"})
    return content


def _apply_tags(user_id: str, raw_content: str) -> str:
    clean_reply, mood_change, updates = extract_tags_and_clean(raw_content)

    if mood_change is not None:
        mood_change = clamp(mood_change, -3, 3)
        new_total = mood_manager.update_mood(user_id, mood_change)
        logger.opt(colors=True).info(
            f"<b><green>🎭 情绪更新：</green></b> {mood_change} | "
            f"<cyan>用户 {user_id} 当前总值：</cyan> {new_total}"
        )

    if updates:
        for k, v in updates:
            save_profile_item(user_id, k, v)
            logger.opt(colors=True).info(
                f"<b><blue>📝 记忆更新：</blue></b> 记住了 {user_id} 的 {k} = {v}"
            )

    return clean_reply.strip()


async def generate_image_reply(user_id: str, image_urls: list[str], user_text: str) -> str:
    """调用 Qwen-VL2 生成图片理解回复（含人设 + 标签清洗）。"""
    try:
        api_key, _, model, max_edge, max_download, max_output_tokens, jpeg_quality, max_images, _ = _load_vl_settings()
    except Exception as e:
        logger.error(f"[vision] load settings failed: {e}")
        return "唔…我这边看图配置有点问题，你叫管理员帮我看看吧。"

    urls = [u for u in image_urls if u][: max(1, max_images)]
    if not urls:
        return "我没有看到图片耶，你再发一次给我看看？"

    data_urls: list[str] = []
    try:
        for url in urls:
            raw = await _download_image(url, max_download)
            jpeg = _compress_to_jpeg(raw, max_edge=max_edge, quality=jpeg_quality)
            data_urls.append(_to_data_url_jpeg(jpeg))
    except ValueError:
        return "这张图有点大，我处理不太动啦…可以稍微压缩一下再发我吗？"
    except RuntimeError as e:
        logger.error(f"[vision] preprocess missing dependency: {e}")
        return "我这边还没装好看图的组件…你叫管理员先装一下 Pillow 好吗？"
    except httpx.HTTPError as e:
        logger.error(f"[vision] download failed: {e}")
        return "图片下载失败了…你可以再发一次吗？"
    except Exception as e:
        logger.error(f"[vision] preprocess failed: {e}")
        return "我刚刚看图时卡了一下…你换张清晰点的再发我试试？"

    try:
        client = _get_vl_client()
    except Exception as e:
        logger.error(f"[vision] init client failed: {e}")
        if not api_key:
            return "我已经看到图片啦，但看图的钥匙还没配好（DASHSCOPE_API_KEY）。"
        return "我看图的通道好像没连上，你稍等我一下～"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": VISION_SYSTEM_PROMPT},
        {"role": "system", "content": _build_system_context(user_id)},
        {"role": "user", "content": _build_user_content(data_urls, user_text)},
    ]

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_output_tokens,
            temperature=0.6,
            timeout=30.0,
        )
        raw = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.error(f"[vision] llm call failed: {e}")
        return "我刚刚看图时卡住了…你等我一下或者再发一次好不好？"

    cleaned = _apply_tags(user_id, raw)
    return cleaned or "唔…我刚刚没看清，你再发一张好吗？"
