"""HTTP API：新增对话入口（例如 STM32）。

这里刻意保持“最小实现”，并且复用与 QQ 完全相同的对话核心：
- 记忆（SQLite chat_history）
- 情绪（mood）
- 用户画像（profile）
- 人格/系统提示词（persona/system prompts）
"""

from __future__ import annotations

import os

from fastapi import Header, HTTPException
from pydantic import BaseModel, Field

from nonebot import get_driver, logger

from .llm_core import get_ai_reply


class ChatRequest(BaseModel):
    text: str = Field(..., description="用户输入文本")
    user_id: str = Field(..., description="会话桶 ID；要与 QQ 连续就填同一个 QQ user_id")
    source: str | None = Field(default=None, description="来源标识，例如 'stm32'")


def _require_api_key(x_api_key: str | None) -> None:
    expected = (os.getenv("STM32_API_KEY") or "").strip()
    if not expected:
        # 失败默认关闭：未配置密钥时，不要“意外对外暴露”对话接口。
        raise HTTPException(status_code=503, detail="STM32_API_KEY is not configured")
    if not x_api_key or x_api_key.strip() != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


driver = get_driver()
app = getattr(driver, "server_app", None)
if app is None:
    # FastAPI driver 下理论上不会发生，但这里保持 import-safe。
    logger.warning("[http_api] driver.server_app is None; /api/chat will not be registered")
else:

    @app.post("/api/chat")
    async def api_chat(
        req: ChatRequest,
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> dict:
        _require_api_key(x_api_key)

        user_id = (req.user_id or "").strip()
        text = (req.text or "").strip()
        source = (req.source or "").strip()

        if not user_id:
            raise HTTPException(status_code=422, detail="user_id is required")
        if not text:
            raise HTTPException(status_code=422, detail="text is required")

        logger.info(f"[api_chat] source={source or '-'} user_id={user_id!r} text={text[:200]!r}")
        reply = await get_ai_reply(user_id, text)
        return {"reply": (reply or "").strip()}
