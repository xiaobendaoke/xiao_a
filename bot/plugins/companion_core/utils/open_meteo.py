"""Open-Meteo 天气查询（异步）。

用途：
- 给 `utils.world_info.get_world_prompt()` 提供“可用的当地天气”。
- 给早晨定时提醒提供结构化天气数据。

实现原则：
- 只做数据获取与轻量格式化，不掺入人设文案（文案交给 LLM 或上层模板）。
- 内置短 TTL 缓存，避免每条消息都请求一次外部 API。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

GEOCODE_BASE = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_BASE = "https://api.open-meteo.com/v1/forecast"

_CACHE_TTL_SECONDS = 10 * 60
_geocode_cache: dict[str, dict[str, Any]] = {}  # key -> {ts, data}
_forecast_cache: dict[str, dict[str, Any]] = {}  # key -> {ts, data}


@dataclass(frozen=True)
class Location:
    name: str
    latitude: float
    longitude: float
    timezone: str
    country: str | None = None
    admin1: str | None = None


def _cache_get(cache: dict[str, dict[str, Any]], key: str) -> Any | None:
    item = cache.get(key)
    if not item:
        return None
    ts = float(item.get("ts") or 0.0)
    if ts and (time.time() - ts) < _CACHE_TTL_SECONDS:
        return item.get("data")
    cache.pop(key, None)
    return None


def _cache_set(cache: dict[str, dict[str, Any]], key: str, data: Any) -> None:
    cache[key] = {"ts": time.time(), "data": data}


def weather_code_to_zh(code: Any) -> str:
    """把 Open-Meteo 的 WMO weather_code 转成中文描述（不求完美，但足够给 LLM 用）。"""
    try:
        c = int(code)
    except Exception:
        return "未知"

    mapping = {
        0: "晴",
        1: "大部晴朗",
        2: "多云",
        3: "阴",
        45: "有雾",
        48: "雾凇",
        51: "毛毛雨（小）",
        53: "毛毛雨（中）",
        55: "毛毛雨（大）",
        56: "冻毛毛雨（小）",
        57: "冻毛毛雨（大）",
        61: "小雨",
        63: "中雨",
        65: "大雨",
        66: "冻雨（小）",
        67: "冻雨（大）",
        71: "小雪",
        73: "中雪",
        75: "大雪",
        77: "雪粒",
        80: "阵雨（小）",
        81: "阵雨（中）",
        82: "阵雨（大）",
        85: "阵雪（小）",
        86: "阵雪（大）",
        95: "雷暴",
        96: "雷暴伴小冰雹",
        99: "雷暴伴大冰雹",
    }
    return mapping.get(c, "未知")

def _open_meteo_proxy() -> str:
    """读取 Open-Meteo 专用代理配置（可选）。"""
    return (
        os.getenv("OPEN_METEO_PROXY")
        or os.getenv("WEATHER_PROXY")
        or os.getenv("GOOGLE_CSE_PROXY")
        or os.getenv("HTTPS_PROXY")
        or os.getenv("HTTP_PROXY")
        or ""
    ).strip()


def get_open_meteo_proxy() -> str:
    """对外暴露当前 Open-Meteo 代理地址（用于日志诊断）。"""
    return _open_meteo_proxy()


async def geocode_city(name: str, *, language: str = "zh", count: int = 1) -> Optional[Location]:
    """用 Open-Meteo Geocoding 把“城市名”解析为经纬度与时区。"""
    q = (name or "").strip()
    if not q:
        return None

    cache_key = f"{q}|{language}|{count}"
    cached = _cache_get(_geocode_cache, cache_key)
    if cached:
        return cached

    params = {"name": q, "count": str(int(count)), "language": language, "format": "json"}
    try:
        proxy = _open_meteo_proxy()
        async with httpx.AsyncClient(
            follow_redirects=True,
            proxy=(proxy or None),
            trust_env=not bool(proxy),
        ) as client:
            r = await client.get(GEOCODE_BASE, params=params, timeout=10.0)
            r.raise_for_status()
            data = r.json() or {}
    except Exception:
        _cache_set(_geocode_cache, cache_key, None)
        return None

    results = data.get("results") or []
    if not results:
        _cache_set(_geocode_cache, cache_key, None)
        return None

    it = results[0] or {}
    loc = Location(
        name=str(it.get("name") or q),
        latitude=float(it.get("latitude")),
        longitude=float(it.get("longitude")),
        timezone=str(it.get("timezone") or "auto"),
        country=str(it.get("country")) if it.get("country") else None,
        admin1=str(it.get("admin1")) if it.get("admin1") else None,
    )
    _cache_set(_geocode_cache, cache_key, loc)
    return loc


async def fetch_forecast(loc: Location) -> dict[str, Any]:
    """拉取当前 + 今日预报（结构化 JSON 原样返回，便于上层选择字段）。"""
    cache_key = f"{loc.latitude:.4f},{loc.longitude:.4f}|{loc.timezone}"
    cached = _cache_get(_forecast_cache, cache_key)
    if cached:
        return cached

    params = {
        "latitude": str(loc.latitude),
        "longitude": str(loc.longitude),
        "timezone": loc.timezone or "auto",
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "forecast_days": "1",
    }
    proxy = _open_meteo_proxy()
    async with httpx.AsyncClient(
        follow_redirects=True,
        proxy=(proxy or None),
        trust_env=not bool(proxy),
    ) as client:
        r = await client.get(FORECAST_BASE, params=params, timeout=12.0)
        r.raise_for_status()
        data = r.json() or {}

    _cache_set(_forecast_cache, cache_key, data)
    return data


def summarize_today_weather(loc: Location, forecast: dict[str, Any]) -> dict[str, Any]:
    """把 forecast 里“当前 + 今日”抽成更稳定的字段，供 world_info/LLM 使用。"""
    current = forecast.get("current") or {}
    daily = forecast.get("daily") or {}

    def pick_daily(name: str):
        v = daily.get(name)
        if isinstance(v, list) and v:
            return v[0]
        return None

    current_code = current.get("weather_code")
    today_code = pick_daily("weather_code")
    code = today_code if today_code is not None else current_code

    out = {
        "city": loc.name,
        "timezone": forecast.get("timezone") or loc.timezone,
        "time": current.get("time") or "",
        "current_temp": current.get("temperature_2m"),
        "current_feels_like": current.get("apparent_temperature"),
        "current_wind_speed": current.get("wind_speed_10m"),
        "current_weather_code": current_code,
        "today_weather_code": today_code,
        "today_weather_text": weather_code_to_zh(code),
        "today_temp_max": pick_daily("temperature_2m_max"),
        "today_temp_min": pick_daily("temperature_2m_min"),
        "today_precip_prob_max": pick_daily("precipitation_probability_max"),
    }
    return out
