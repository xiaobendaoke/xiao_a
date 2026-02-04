"""语音合成（TTS）。

职责：
- 调用 Qwen realtime TTS（qwen3-tts-vc-realtime-*）合成 24k/16bit/mono PCM
- 写入 WAV，并尽量转成 mp3
- 返回 OneBot 可直接发送的 `base64://...`（用于 `MessageSegment.record`）
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path

from nonebot import logger


def _env(name: str, default: str = "") -> str:
    v = (os.getenv(name) or default).strip()
    return v.split()[0] if v else ""


def _clamp_float(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(v)))


def _clamp_int(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(v)))


def _env_float(name: str, default: float | None = None) -> float | None:
    v = _env(name)
    if not v:
        return default
    try:
        return float(v)
    except Exception:
        return default


def _env_int(name: str, default: int | None = None) -> int | None:
    v = _env(name)
    if not v:
        return default
    try:
        return int(v)
    except Exception:
        return default


def _env_bool(name: str, default: bool | None = None) -> bool | None:
    v = _env(name)
    if not v:
        return default
    if v.lower() in ("1", "true", "yes", "y", "on"):
        return True
    if v.lower() in ("0", "false", "no", "n", "off"):
        return False
    return default


_TAG_LINE_RE = re.compile(r"^\s*\[(?:MOOD_CHANGE|UPDATE_PROFILE):.*\]\s*$", re.IGNORECASE)
_URL_RE = re.compile(r"https?://\S+")


def clean_for_tts(text: str) -> str:
    """给 TTS 用的文本清洗：去掉括号动作、标签行、链接，避免读出“（戳戳屏幕）”这种舞台指示。"""
    s = (text or "").strip()
    if not s:
        return ""

    lines = []
    for line in s.splitlines():
        if _TAG_LINE_RE.match(line):
            continue
        lines.append(line)
    s = "\n".join(lines)

    # 去链接
    s = _URL_RE.sub("", s)

    # 去常见“动作/旁白”括号：（） () 【】 []（注意：[] 已单独用于标签行，这里只做温和清理）
    # 只要括号里有中文/英文就去掉整段，避免读出台词外内容。
    s = re.sub(r"（[^）]{0,80}）", "", s)
    s = re.sub(r"\\([^\\)]{0,80}\\)", "", s)
    s = re.sub(r"【[^】]{0,80}】", "", s)

    # 去掉常见的颜文字/Kaomoji (如 ( -ω- ) 或 ~ )
    # 1. 移除 Unicode Emoji (简单的范围)
    s = re.sub(r"[\\U00010000-\\U0010ffff]", "", s)
    # 2. 移除一些特殊的符号组合，如 " - " 或 "~"
    s = re.sub(r"([~～]+)", " ", s)
    # 3. 移除特定的颜文字片段 (如 "-ω-", "-_-", "QAQ", "OvO")
    # 简单策略：移除连续的非中文/非单词字符组合，如果它们看起来像表情
    # 但不要误伤英文单词。
    # 针对 "减欧米伽" (-Ω)，移除以 - 开头的特殊字符
    s = re.sub(r"(^|\\s)[-−][A-Za-z0-9Ωω]+(?=\\s|$)", "", s)
    s = re.sub(r"[-−]{2,}", "", s)  # 多个减号

    # 合并空白
    s = re.sub(r"[ \\t]+", " ", s)
    s = re.sub(r"\\n{3,}", "\n\n", s)
    s = s.strip()
    return s


def _ensure_pause_punctuation(text: str, mood: int | None) -> str:
    s = (text or "").strip()
    if not s:
        return ""

    # 换行作为“强停顿”，但确保每行末尾有标点
    lines = [ln.strip() for ln in s.splitlines()]
    out: list[str] = []
    end_punct = ("。", "！", "？", "…", ".", "!", "?")
    for ln in lines:
        if not ln:
            continue
        if ln.endswith(end_punct):
            out.append(ln)
        else:
            out.append(ln + ("！" if (mood is not None and mood >= 30) else "。"))
    s = "\n".join(out).strip()

    # 把三个点/六个点统一成中文省略号（更像口语停顿）
    s = s.replace("......", "……").replace("...", "……")
    return s


def _mood_to_tts_overrides(
    mood: int | None,
    *,
    base_speech_rate: float,
    base_pitch_rate: float,
    base_volume: int,
) -> tuple[float, float, int]:
    if mood is None:
        return base_speech_rate, base_pitch_rate, base_volume

    # 归一化到 [-1, 1]，让参数变化不过分夸张
    m = _clamp_float(float(mood) / 60.0, -1.0, 1.0)

    speech_rate = base_speech_rate * (1.0 + 0.12 * m)
    pitch_rate = base_pitch_rate * (1.0 + 0.10 * m)
    volume = base_volume + int(round(10.0 * m))

    speech_rate = _clamp_float(speech_rate, 0.5, 2.0)
    pitch_rate = _clamp_float(pitch_rate, 0.5, 2.0)
    volume = _clamp_int(volume, 0, 100)
    return speech_rate, pitch_rate, volume


def _ws_url(region: str) -> str:
    r = (region or "cn").strip().lower()
    if r in ("intl", "sg", "singapore"):
        return "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime"
    return "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"


class _CollectorCallback:
    def __init__(self):
        self.finished = threading.Event()
        self.buf = bytearray()
        self.errors: list[dict] = []

    def on_open(self) -> None:
        pass

    def on_close(self, close_status_code, close_msg) -> None:
        self.finished.set()

    def on_event(self, response: dict) -> None:
        try:
            t = response.get("type", "")
            if t == "response.audio.delta":
                self.buf.extend(base64.b64decode(response.get("delta") or ""))
            elif t == "session.finished":
                self.finished.set()
            elif response.get("error"):
                self.errors.append(response)
        except Exception:
            self.finished.set()

    def wait(self, timeout: float | None = None) -> bool:
        return self.finished.wait(timeout=timeout)


def _write_wav(path: Path, pcm: bytes, *, sample_rate: int = 24000, channels: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(int(channels))
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(int(sample_rate))
        wf.writeframes(pcm)


def _wav_to_mp3(wav_path: Path, mp3_path: Path) -> None:
    mp3_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-i",
        str(wav_path),
        "-ac",
        "1",
        "-ar",
        "24000",
        "-b:a",
        "64k",
        str(mp3_path),
    ]
    subprocess.run(cmd, check=True)


def _synthesize_pcm_sync(
    *,
    text: str,
    voice: str,
    model: str,
    region: str,
    speech_rate: float | None,
    pitch_rate: float | None,
    volume: int | None,
) -> bytes:
    import dashscope  # type: ignore
    from dashscope.audio.qwen_tts_realtime import (  # type: ignore
        AudioFormat,
        QwenTtsRealtime,
        QwenTtsRealtimeCallback,
    )

    api_key = _env("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("Missing env: DASHSCOPE_API_KEY")
    dashscope.api_key = api_key

    collector = _CollectorCallback()

    class Callback(QwenTtsRealtimeCallback):  # type: ignore[misc]
        def on_open(self) -> None:  # type: ignore[override]
            collector.on_open()

        def on_close(self, close_status_code, close_msg) -> None:  # type: ignore[override]
            collector.on_close(close_status_code, close_msg)

        def on_event(self, response: dict) -> None:  # type: ignore[override]
            collector.on_event(response)

    cb = Callback()
    tts = QwenTtsRealtime(model=model, callback=cb, url=_ws_url(region))
    tts.connect()
    tts.update_session(
        voice=voice,
        response_format=AudioFormat.PCM_24000HZ_MONO_16BIT,
        mode="server_commit",
        volume=volume,
        speech_rate=speech_rate,
        pitch_rate=pitch_rate,
        enable_tn=_env_bool("QWEN_TTS_ENABLE_TN"),
        language_type=_env("QWEN_TTS_LANGUAGE_TYPE") or None,
    )
    tts.append_text(text)
    time.sleep(0.05)
    try:
        tts.commit()
    except Exception:
        pass
    tts.finish()
    collector.wait(timeout=60.0)

    if not collector.buf:
        raise RuntimeError(f"No audio received from TTS. errors={collector.errors!r}")
    return bytes(collector.buf)


async def synthesize_record_base64(text: str, *, mood: int | None = None) -> str:
    """用 Qwen realtime TTS 合成语音，并返回 OneBot 可用的 base64:// 音频（mp3）。"""
    voice = _env("QWEN_TTS_VOICE")
    if not voice:
        raise RuntimeError("Missing env: QWEN_TTS_VOICE (use output.voice from voice cloning)")
    model = _env("QWEN_TTS_MODEL", "qwen3-tts-vc-realtime-2025-11-27")
    region = _env("DASHSCOPE_REGION", "cn")

    clean_text = _ensure_pause_punctuation(clean_for_tts(text), mood)
    if not clean_text:
        raise RuntimeError("Empty TTS text after cleaning")

    base_speech_rate = _env_float("QWEN_TTS_SPEECH_RATE", 1.0) or 1.0
    base_pitch_rate = _env_float("QWEN_TTS_PITCH_RATE", 1.0) or 1.0
    base_volume = _env_int("QWEN_TTS_VOLUME", 50) or 50
    speech_rate, pitch_rate, volume = _mood_to_tts_overrides(
        mood,
        base_speech_rate=base_speech_rate,
        base_pitch_rate=base_pitch_rate,
        base_volume=base_volume,
    )

    logger.info(
        f"[tts] mood={mood} speech_rate={speech_rate:.2f} pitch_rate={pitch_rate:.2f} volume={volume}"
    )

    pcm = await asyncio.to_thread(
        _synthesize_pcm_sync,
        text=clean_text,
        voice=voice,
        model=model,
        region=region,
        speech_rate=speech_rate,
        pitch_rate=pitch_rate,
        volume=volume,
    )

    with tempfile.TemporaryDirectory(prefix="qqbot_tts_") as td:
        wav_path = Path(td) / "out.wav"
        mp3_path = Path(td) / "out.mp3"
        _write_wav(wav_path, pcm)
        try:
            await asyncio.to_thread(_wav_to_mp3, wav_path, mp3_path)
            data = mp3_path.read_bytes()
        except Exception as e:
            logger.warning(f"[tts] mp3 transcode failed, fallback to wav: {e}")
            data = wav_path.read_bytes()

    return "base64://" + base64.b64encode(data).decode("ascii")
