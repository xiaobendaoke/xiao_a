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
        self.errors: list[dict] = []
        self.events: list[str] = []
        self.closed: tuple[object, object] | None = None

    def on_open(self) -> None:
        self.events.append("ws.open")

    def on_close(self, close_status_code, close_msg) -> None:
        self.closed = (close_status_code, close_msg)
        self.events.append("ws.close")
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
            else:
                # 记录服务端事件，便于排错
                self.events.append(t or "unknown")
                if response.get("error"):
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


def main() -> int:
    p = argparse.ArgumentParser(description="Synthesize speech via Qwen-TTS Realtime and save WAV.")
    p.add_argument("--voice", default=_env("QWEN_TTS_VOICE"), help="复刻音色（output.voice）")
    p.add_argument("--model", required=True, help="TTS realtime 模型（需与复刻时 target_model 一致）")
    p.add_argument("--text", action="append", default=[], help="要合成的文本（可重复传多次，按顺序拼接）")
    p.add_argument("--out", default="out.wav", help="输出 wav 路径")
    p.add_argument("--region", default=_env("DASHSCOPE_REGION", "cn"), help="cn/intl（默认 cn）")
    p.add_argument("--mode", default="server_commit", help="server_commit/commit（默认 server_commit）")
    p.add_argument("--timeout", type=float, default=60.0, help="等待服务端合成秒数（默认 60）")
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

    class Callback(QwenTtsRealtimeCallback):  # type: ignore[misc]
        def __init__(self, collector: _CollectorCallback):
            super().__init__()
            self._c = collector

        def on_open(self) -> None:  # type: ignore[override]
            self._c.on_open()

        def on_close(self, close_status_code, close_msg) -> None:  # type: ignore[override]
            self._c.on_close(close_status_code, close_msg)

        def on_event(self, response: dict) -> None:  # type: ignore[override]
            self._c.on_event(response)

    collector = _CollectorCallback()
    cb = Callback(collector)
    tts = QwenTtsRealtime(model=args.model, callback=cb, url=_ws_url(args.region))
    tts.connect()

    tts.update_session(
        voice=args.voice,
        response_format=AudioFormat.PCM_24000HZ_MONO_16BIT,
        mode=args.mode,
    )

    for chunk in texts:
        tts.append_text(chunk)
        time.sleep(0.05)

    # 为兼容不同服务端/模式，主动 commit 一次；server_commit 下通常也可接受。
    try:
        tts.commit()
    except Exception:
        pass

    tts.finish()
    collector.wait(timeout=float(args.timeout))

    out = Path(args.out).expanduser().resolve()
    if not collector.buf:
        if collector.errors:
            print("No audio received. Errors:")
            for err in collector.errors:
                print(err)
        else:
            print("No audio received. Events:", collector.events)
            if collector.closed is not None:
                print("WebSocket closed:", collector.closed[0], collector.closed[1])
        raise SystemExit(2)

    _write_wav(out, bytes(collector.buf))
    print(f"Saved: {out} ({len(collector.buf)} bytes PCM)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
