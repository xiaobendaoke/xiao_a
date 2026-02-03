from __future__ import annotations
from nonebot import logger

# Reuse methods from companion_core if possible, or define wrappers
# Assuming companion_core has a central LLM client manager
# Based on existing imports in other files, we can probably import from parent?
# But finance_daily was standalone.
# Let's try to import from companion_core.llm_client 

try:
    from ...llm_client import get_client, load_llm_settings
except ImportError:
    # Fallback or stub if not found
    import os
    from openai import AsyncOpenAI
    
    def get_client():
        return AsyncOpenAI(
            api_key=os.getenv("LLM_API_KEY"),
            base_url=os.getenv("LLM_BASE_URL")
        )

    def load_llm_settings():
        return {}, {}, os.getenv("LLM_MODEL", "gpt-3.5-turbo")

