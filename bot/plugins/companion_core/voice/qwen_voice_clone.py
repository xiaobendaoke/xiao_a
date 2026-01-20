#!/usr/bin/env python3
"""Qwen-TTS 声音复刻：创建音色（voice enrollment）。

对应文档：Qwen-TTS 声音复刻 API（创建音色）
https://help.aliyun.com/zh/model-studio/qwen-tts-voice-cloning

做什么：
- 读取本地音频文件（WAV/MP3/M4A 等）
- base64 编码成 data URI
- 调用 DashScope REST：/api/v1/services/audio/tts/customization
- 输出 response JSON，并打印 `output.voice`（后续语音合成需要）

用法示例：
  export DASHSCOPE_API_KEY='sk-xxx'
  python3 scripts/qwen_voice_clone.py --file voice.mp3 --target-model qwen3-tts-vc-realtime-2025-11-27 --name xiao_a

可选：如果你的 Key 在海外区，改 `--region intl`（默认 cn）。
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
from pathlib import Path
from typing import Any

import httpx


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _guess_mime(path: Path, override: str | None) -> str:
    if override:
        return override
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "audio/mpeg"


def _data_uri(path: Path, mime: str) -> str:
    b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def _endpoint(region: str) -> str:
    # 文档示例：北京 https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization
    #         新加坡 https://dashscope-intl.aliyuncs.com/api/v1/services/audio/tts/customization
    r = (region or "cn").strip().lower()
    if r in ("intl", "sg", "singapore"):
        return "https://dashscope-intl.aliyuncs.com/api/v1/services/audio/tts/customization"
    return "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization"


def build_payload(
    *,
    target_model: str,
    preferred_name: str,
    audio_data_uri: str,
    text: str | None,
    language: str | None,
) -> dict[str, Any]:
    input_obj: dict[str, Any] = {
        "action": "create",
        "target_model": target_model,
        "preferred_name": preferred_name,
        "audio": {"data": audio_data_uri},
    }
    if text:
        input_obj["text"] = text
    if language:
        input_obj["language"] = language

    return {"model": "qwen-voice-enrollment", "input": input_obj}


async def main() -> int:
    p = argparse.ArgumentParser(description="Create voice via Qwen-TTS voice cloning (DashScope).")
    p.add_argument("--file", required=True, help="音频文件路径（WAV/MP3/M4A），推荐 10~20 秒、<=10MB")
    p.add_argument("--target-model", required=True, help="驱动音色的语音合成模型（后续合成必须用同一个）")
    p.add_argument("--name", required=True, help="音色名称（preferred_name）")
    p.add_argument("--region", default=_env("DASHSCOPE_REGION", "cn"), help="cn/intl（默认 cn）")
    p.add_argument("--mime", default="", help="音频 mime（可选），如 audio/mpeg audio/wav")
    p.add_argument("--text", default="", help="可选：音频对应文本（提升效果）")
    p.add_argument("--language", default="", help="可选：语种，如 zh/en")
    p.add_argument("--timeout", type=float, default=60.0, help="请求超时秒数")
    p.add_argument("--proxy", default=_env("DASHSCOPE_PROXY") or _env("HTTPS_PROXY") or _env("HTTP_PROXY"), help="可选代理")
    p.add_argument("--out", default="", help="可选：把完整响应 JSON 保存到文件")
    args = p.parse_args()

    api_key = _env("DASHSCOPE_API_KEY")
    if not api_key:
        raise SystemExit("Missing env: DASHSCOPE_API_KEY")

    path = Path(args.file).expanduser().resolve()
    if not path.exists():
        raise SystemExit(f"File not found: {path}")

    mime = _guess_mime(path, args.mime.strip() or None)
    audio_uri = _data_uri(path, mime)
    url = _endpoint(args.region)
    payload = build_payload(
        target_model=args.target_model.strip(),
        preferred_name=args.name.strip(),
        audio_data_uri=audio_uri,
        text=(args.text.strip() or None),
        language=(args.language.strip() or None),
    )

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    timeout = httpx.Timeout(args.timeout, connect=10.0)
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        proxy=(args.proxy or None),
        trust_env=not bool(args.proxy),
    ) as client:
        resp = await client.post(url, json=payload, headers=headers)

    if resp.status_code != 200:
        raise SystemExit(f"HTTP {resp.status_code}: {resp.text}")

    data = resp.json() if resp.content else {}
    if args.out:
        Path(args.out).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    voice = (((data or {}).get("output") or {}).get("voice")) if isinstance(data, dict) else None
    print(json.dumps(data, ensure_ascii=False, indent=2))
    print("\n---\noutput.voice:\n")
    print(voice or "")
    return 0


if __name__ == "__main__":
    raise SystemExit(__import__("asyncio").run(main()))

