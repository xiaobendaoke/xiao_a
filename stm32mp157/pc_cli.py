#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import socket
import sys
import time
from typing import Optional, Tuple

DEFAULT_TIMEOUT = 5.0

def now_ms() -> int:
    return int(time.time() * 1000)

def recv_line(sock: socket.socket, timeout: float = DEFAULT_TIMEOUT) -> Optional[str]:
    """Receive until '\n' or socket close. Returns line without trailing newlines."""
    sock.settimeout(timeout)
    buf = bytearray()
    try:
        while True:
            ch = sock.recv(1)
            if not ch:
                # connection closed
                if not buf:
                    return None
                break
            buf += ch
            if ch == b"\n":
                break
    except socket.timeout:
        # If we already got something, return it; else None
        if not buf:
            return None
    except OSError:
        return None

    return buf.decode(errors="ignore").rstrip("\r\n")

def connect(host: str, port: int, timeout: float = DEFAULT_TIMEOUT) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect((host, port))
    # after connect, switch to blocking with timeout control in recv_line
    s.settimeout(None)
    return s

def send_line(sock: socket.socket, text: str) -> None:
    if not text.endswith("\n"):
        text += "\n"
    sock.sendall(text.encode())

def pretty_json_if_possible(s: str) -> str:
    s_strip = s.strip()
    if not s_strip:
        return s
    # Try parse JSON for nicer print (optional)
    try:
        obj = json.loads(s_strip)
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return s

def interactive_loop(host: str, port: int, timeout: float, reconnect: bool) -> int:
    sock: Optional[socket.socket] = None

    def ensure_conn() -> bool:
        nonlocal sock
        if sock is not None:
            return True
        while True:
            try:
                sock = connect(host, port, timeout=timeout)
                # read greeting if any (non-blocking-ish)
                greet = recv_line(sock, timeout=0.5)
                if greet is not None:
                    print(f"[<-] {greet}")
                print(f"[ok] connected to {host}:{port}")
                return True
            except Exception as e:
                print(f"[!] connect failed: {e}")
                if not reconnect:
                    return False
                time.sleep(1.0)

    print("=== XiaoA PC Debug CLI ===")
    print("输入示例：")
    print("  PING")
    print('  {"id":1,"cmd":"ping"}')
    print("  /reconnect on|off   开关断线重连")
    print("  /timeout 5          设置接收超时(秒)")
    print("  /quit               退出")
    print("-------------------------")

    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[bye]")
            return 0

        if not line:
            continue

        # commands
        if line in ("/q", "/quit", "quit", "exit"):
            print("[bye]")
            return 0

        if line.startswith("/reconnect"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].lower() in ("on", "off"):
                reconnect = (parts[1].lower() == "on")
                print(f"[ok] reconnect={'on' if reconnect else 'off'}")
            else:
                print("[usage] /reconnect on|off")
            continue

        if line.startswith("/timeout"):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    timeout = float(parts[1])
                    print(f"[ok] timeout={timeout}s")
                except ValueError:
                    print("[!] timeout must be a number")
            else:
                print("[usage] /timeout 5")
            continue

        # ensure connection
        if not ensure_conn():
            return 2

        # send
        assert sock is not None
        t0 = now_ms()
        try:
            send_line(sock, line)
            print(f"[->] {line}")
        except Exception as e:
            print(f"[!] send failed: {e}")
            try:
                sock.close()
            except Exception:
                pass
            sock = None
            continue

        # recv one response line (你的链路目前是一问一答模型，先按 1 行收)
        try:
            resp = recv_line(sock, timeout=timeout)
            t1 = now_ms()
            if resp is None:
                print(f"[<-] (no response, {t1 - t0} ms)")
            else:
                # 尝试美化 JSON（如果不是 JSON 就原样）
                pretty = pretty_json_if_possible(resp)
                if pretty != resp:
                    print(f"[<-] (json, {t1 - t0} ms)\n{pretty}")
                else:
                    print(f"[<-] {resp}  ({t1 - t0} ms)")
        except Exception as e:
            print(f"[!] recv failed: {e}")
            try:
                sock.close()
            except Exception:
                pass
            sock = None
            continue

def one_shot(host: str, port: int, msg: str, timeout: float) -> int:
    try:
        sock = connect(host, port, timeout=timeout)
        greet = recv_line(sock, timeout=0.5)
        if greet is not None:
            print(f"[<-] {greet}")
        t0 = now_ms()
        send_line(sock, msg)
        print(f"[->] {msg}")
        resp = recv_line(sock, timeout=timeout)
        t1 = now_ms()
        if resp is None:
            print(f"[<-] (no response, {t1 - t0} ms)")
            return 3
        print(f"[<-] {resp}  ({t1 - t0} ms)")
        return 0
    except Exception as e:
        print(f"[!] error: {e}")
        return 2
    finally:
        try:
            sock.close()
        except Exception:
            pass

def main() -> int:
    ap = argparse.ArgumentParser(description="XiaoA PC Debug CLI (TCP)")
    ap.add_argument("--host", default="192.168.1.50", help="STM32MP157 IP (bridge)")
    ap.add_argument("--port", type=int, default=9000, help="bridge port")
    ap.add_argument("--timeout", type=float, default=5.0, help="recv timeout seconds")
    ap.add_argument("--no-reconnect", action="store_true", help="disable auto reconnect")
    ap.add_argument("--send", default=None, help="one-shot send message then exit")
    args = ap.parse_args()

    if args.send is not None:
        return one_shot(args.host, args.port, args.send, args.timeout)

    return interactive_loop(args.host, args.port, args.timeout, reconnect=(not args.no_reconnect))

if __name__ == "__main__":
    raise SystemExit(main())
