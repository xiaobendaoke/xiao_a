"""LLM Prompts 模块。

整合各类场景的 prompt 生成函数。

结构：
- persona: 人设相关
- weather: 天气播报
- stock: 股票分析
- vision: 图像分析
- web: 网页/RSS 分析
- proactive: 主动消息
- news: 新闻分析
"""

# === 从现有模块导入 ===
# 注意：这里是 llm/prompts.py，相对于 companion_core 是两级

# 人设
from ..persona import SYSTEM_PROMPT as PERSONA_PROMPT

# 天气播报
from ..llm_weather import (
    generate_morning_weather_text,
    WEATHER_PUSH_SYSTEM,
    WEATHER_QA_SYSTEM,
)

# 股票分析
from ..llm_stock import (
    generate_stock_chat_text,
    STOCK_CHAT_SYSTEM,
)

# 图像分析
from ..llm_vision import (
    generate_image_reply,
)

# 网页/RSS 分析
from ..llm_web import (
    generate_rss_share,
    generate_url_summary,
    generate_url_confirm,
    generate_github_weekly_share,
)

# 主动消息
from ..llm_proactive import (
    generate_proactive_message,
    PROACTIVE_SYSTEM_PROMPT,
)

# 新闻分析
from ..llm_news import (
    maybe_get_web_search_context,
    should_web_search,
    normalize_search_query,
)

__all__ = [
    # 人设
    "PERSONA_PROMPT",
    # 天气
    "generate_morning_weather_text",
    "WEATHER_PUSH_SYSTEM",
    "WEATHER_QA_SYSTEM",
    # 股票
    "generate_stock_chat_text",
    "STOCK_CHAT_SYSTEM",
    # 图像
    "generate_image_reply",
    # 网页
    "generate_rss_share",
    "generate_url_summary",
    "generate_url_confirm",
    "generate_github_weekly_share",
    # 主动消息
    "generate_proactive_message",
    "PROACTIVE_SYSTEM_PROMPT",
    # 新闻
    "maybe_get_web_search_context",
    "should_web_search",
    "normalize_search_query",
]
