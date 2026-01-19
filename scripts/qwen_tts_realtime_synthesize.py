#!/usr/bin/env python3
"""Qwen-TTS Realtime：用复刻音色合成一段语音并保存为 WAV。

前置：你需要先用 `scripts/qwen_voice_clone.py` 拿到 `output.voice`。

依赖：
  pip install "dashscope>=1.23.9"

用法示例：
  export DASHSCOPE_API_KEY='sk-xxx'
  python3 scripts/qwen_tts_realtime_synthesize.py \\
    --voice qwen-tts-vc-xxx \\
    --model qwen3-tts-vc-realtime-2025-11-27 \\
    --text '你好呀，我是小a。' \\
    --out out.wav
"""

from __future__ import annotations

import argparse
import base64
import os
import threading
import time
import wave
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _ws_url(region: str) -> str:
    r = (region or "cn").strip().lower()
    if r in ("intl", "sg", "singapore"):
        return "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime"
    return "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"


class _CollectorCallback:  # QwenTtsRealtimeCallback
    def __init__(self):
        self.finished = threading.Event()
        self.buf = bytearray()

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
            elif t == "response.done":
                # 可能会先到 done，再到 finished
                pass
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


def main() -> int:
    p = argparse.ArgumentParser(description="Synthesize speech via Qwen-TTS Realtime and save WAV.")
    p.add_argument("--voice", default=_env("QWEN_TTS_VOICE"), help="复刻音色（output.voice）")
    p.add_argument("--model", required=True, help="TTS realtime 模型（需与复刻时 target_model 一致）")
    p.add_argument("--text", action="append", default=[], help="要合成的文本（可重复传多次，按顺序拼接）")
    p.add_argument("--out", default="out.wav", help="输出 wav 路径")
    p.add_argument("--region", default=_env("DASHSCOPE_REGION", "cn"), help="cn/intl（默认 cn）")
    args = p.parse_args()

    api_key = _env("DASHSCOPE_API_KEY")
    if not api_key:
        raise SystemExit("Missing env: DASHSCOPE_API_KEY")
    if not args.voice:
        raise SystemExit("Missing --voice (output.voice)")
    texts = [t.strip() for t in (args.text or []) if t and t.strip()]
    if not texts:
        texts = ["你好呀，我是小a。"]

    try:
        import dashscope  # type: ignore
        from dashscope.audio.qwen_tts_realtime import (  # type: ignore
            AudioFormat,
            QwenTtsRealtime,
            QwenTtsRealtimeCallback,
        )
    except Exception as e:
        raise SystemExit(f"Missing dependency: dashscope (pip install 'dashscope>=1.23.9'), err={e}")

    dashscope.api_key = api_key

    class Callback(QwenTtsRealtimeCallback, _CollectorCallback):  # type: ignore[misc]
        pass

    cb = Callback()
    tts = QwenTtsRealtime(model=args.model, callback=cb, url=_ws_url(args.region))
    tts.connect()

    tts.update_session(
        voice=args.voice,
        response_format=AudioFormat.PCM_24000HZ_MONO_16BIT,
        mode="server_commit",
    )

    for chunk in texts:
        tts.append_text(chunk)
        time.sleep(0.05)

    tts.finish()
    cb.wait(timeout=60.0)

    out = Path(args.out).expanduser().resolve()
    _write_wav(out, bytes(cb.buf))
    print(f"Saved: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

