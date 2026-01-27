"""finance_daily 配置（环境变量）。

风格对齐 companion_core：
- 模块导入时读取 env，形成常量；
- 定时任务与管道直接引用常量（改配置后重启生效）。

关键配置：
- `TUSHARE_TOKEN`：Tushare Pro Token（必需）
- `FIN_DAILY_ENABLED`：1/0 开关
- `FIN_DAILY_RUN_HOUR`/`FIN_DAILY_RUN_MINUTE`：每日执行时间（按容器 TZ）
- `FIN_DAILY_TOP_N`：涨跌榜各取 N
- `FIN_DAILY_AMOUNT_MIN`：过滤成交额阈值（元；内部已把 tushare amount 换算为元）
- `FIN_DAILY_NEW_LIST_DAYS`：过滤新股上市天数 < X
- `FIN_DAILY_ANN_LOOKBACK_DAYS`：公告回看天数（TopN 小时按股票拉更省调用）
- `FIN_DAILY_TARGETS`：私聊目标，如 `private:123456`；或写 `all_friends` 开启广播
- `FIN_DAILY_BROADCAST_ALL_FRIENDS`：1/0 显式开启“发给所有好友”（仅私聊）
- `FIN_DAILY_SEND_INTERVAL_SECONDS`：发送节流间隔（秒）
- `FIN_DAILY_BROADCAST_LIMIT`：广播上限（0=不限制）
"""

from __future__ import annotations

import os
import re


def _env_token(name: str, default: str = "") -> str:
    """兼容 `.env` 行尾注释：`KEY=xxx  # comment`。"""
    v = (os.getenv(name) or "").strip()
    if not v:
        return default
    return v.split()[0]


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env_token(name, str(default)) or default)
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env_token(name, str(default)) or default)
    except Exception:
        return default


def _env_bool(name: str, default: bool) -> bool:
    v = _env_token(name, "")
    if not v:
        return bool(default)
    return v.lower() in ("1", "true", "yes", "y", "on")


def _split_tokens(s: str) -> list[str]:
    return [t for t in re.split(r"[\s,，]+", (s or "").strip()) if t]


def parse_targets(raw: str) -> list[tuple[str, int]]:
    """解析推送目标：`private:123456`（仅私聊；群目标会被忽略）。"""
    out: list[tuple[str, int]] = []
    for tok in _split_tokens(raw):
        if ":" not in tok:
            continue
        t, v = tok.split(":", 1)
        t = t.strip().lower()
        v = v.strip()
        if t not in ("private", "group"):
            continue
        try:
            out.append((t, int(v)))
        except Exception:
            continue
    return out


TUSHARE_TOKEN = _env_token("TUSHARE_TOKEN", "")

FIN_DAILY_ENABLED = _env_bool("FIN_DAILY_ENABLED", True)
FIN_DAILY_MARKET = (os.getenv("FIN_DAILY_MARKET") or "CN_A").strip() or "CN_A"
FIN_DAILY_RUN_HOUR = _env_int("FIN_DAILY_RUN_HOUR", 15)
FIN_DAILY_RUN_MINUTE = _env_int("FIN_DAILY_RUN_MINUTE", 20)

FIN_DAILY_TOP_N = max(1, _env_int("FIN_DAILY_TOP_N", 5))
FIN_DAILY_NEW_LIST_DAYS = max(0, _env_int("FIN_DAILY_NEW_LIST_DAYS", 20))
FIN_DAILY_AMOUNT_MIN = max(0.0, _env_float("FIN_DAILY_AMOUNT_MIN", 2e8))
FIN_DAILY_ANN_LOOKBACK_DAYS = max(0, _env_int("FIN_DAILY_ANN_LOOKBACK_DAYS", 7))
FIN_DAILY_PROMPT_VERSION = (os.getenv("FIN_DAILY_PROMPT_VERSION") or "v1").strip() or "v1"
FIN_DAILY_LLM_CONCURRENCY = max(1, _env_int("FIN_DAILY_LLM_CONCURRENCY", 2))
FIN_DAILY_OUTPUT_MODE = (os.getenv("FIN_DAILY_OUTPUT_MODE") or "xiao_a").strip().lower()
FIN_DAILY_CHAT_PER_STOCK = _env_bool("FIN_DAILY_CHAT_PER_STOCK", True)
FIN_DAILY_OVERVIEW_ENABLED = _env_bool("FIN_DAILY_OVERVIEW_ENABLED", True)
FIN_DAILY_ITEM_MAX_CHARS = max(120, _env_int("FIN_DAILY_ITEM_MAX_CHARS", 280))

FIN_DAILY_DATA_PROVIDER = (os.getenv("FIN_DAILY_DATA_PROVIDER") or "eastmoney").strip().lower()
FIN_DAILY_EASTMONEY_PROXY = _env_token("FIN_DAILY_EASTMONEY_PROXY", "") or _env_token("HTTP_PROXY", "")

FIN_DAILY_TARGETS_RAW = (os.getenv("FIN_DAILY_TARGETS") or "").strip()
FIN_DAILY_TARGETS = parse_targets(FIN_DAILY_TARGETS_RAW)

_targets_tokens = _split_tokens(FIN_DAILY_TARGETS_RAW)
_broadcast_in_targets = any(t.lower() in ("all_friends", "friends", "broadcast_all_friends") for t in _targets_tokens)
FIN_DAILY_BROADCAST_ALL_FRIENDS = _env_bool("FIN_DAILY_BROADCAST_ALL_FRIENDS", False) or _broadcast_in_targets
FIN_DAILY_SEND_INTERVAL_SECONDS = max(0.0, _env_float("FIN_DAILY_SEND_INTERVAL_SECONDS", 0.8))
FIN_DAILY_BROADCAST_LIMIT = max(0, _env_int("FIN_DAILY_BROADCAST_LIMIT", 0))  # 0=不限制
