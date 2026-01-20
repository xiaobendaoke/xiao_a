"""语音识别（ASR）。

职责：
- 将 OneBot/NapCat 收到的语音文件转成 16kHz/mono WAV（ffmpeg）
- 调用 DashScope paraformer-realtime-v2 做语音转文字

说明：
- 这里用的是 DashScope SDK 的同步 `Recognition.call()`，再用 `asyncio.to_thread` 包一层，避免阻塞事件循环。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
from pathlib import Path

from nonebot import logger


def _env(name: str, default: str = "") -> str:
    v = (os.getenv(name) or default).strip()
    # 兼容 `.env` 行尾注释/空格：`KEY=xxx  # comment`
    return v.split()[0] if v else ""


def _ffmpeg_convert_to_wav16k_mono(in_path: Path, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-i",
        str(in_path),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "wav",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)


def _parse_recognition_text(result) -> str:
    try:
        sentences = result.get_sentence()  # type: ignore[attr-defined]
    except Exception:
        sentences = None

    texts: list[str] = []
    if isinstance(sentences, list):
        for s in sentences:
            if isinstance(s, dict):
                t = str(s.get("text") or "").strip()
                if t:
                    texts.append(t)
    elif isinstance(sentences, dict):
        t = str(sentences.get("text") or "").strip()
        if t:
            texts.append(t)
    return " ".join(texts).strip()


def _recognize_sync(wav16k_path: Path, *, model: str) -> str:
    import dashscope  # type: ignore
    from dashscope.audio.asr import Recognition, RecognitionCallback  # type: ignore

    api_key = _env("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("Missing env: DASHSCOPE_API_KEY")
    dashscope.api_key = api_key

    cb = RecognitionCallback()
    rec = Recognition(model=model, callback=cb, format="wav", sample_rate=16000)
    result = rec.call(file=str(wav16k_path))
    text = _parse_recognition_text(result)
    return text


async def transcribe_audio_file(in_path: Path) -> str:
    """将任意音频文件转成 16k mono wav 后，用 paraformer-realtime-v2 转写为文本。"""
    model = _env("DASHSCOPE_ASR_MODEL", "paraformer-realtime-v2")
    with tempfile.TemporaryDirectory(prefix="qqbot_asr_") as td:
        wav_path = Path(td) / "in.wav"
        try:
            await asyncio.to_thread(_ffmpeg_convert_to_wav16k_mono, in_path, wav_path)
        except Exception as e:
            logger.warning(f"[asr] ffmpeg convert failed: {e}")
            raise

        text = await asyncio.to_thread(_recognize_sync, wav_path, model=model)
        return (text or "").strip()

