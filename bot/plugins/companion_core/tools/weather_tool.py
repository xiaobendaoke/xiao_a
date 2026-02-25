"""天气查询工具。

封装现有 llm_weather 的天气查询能力。
"""

from __future__ import annotations

from ..tool_registry import register_tool, ToolParam
from ..utils.world_info import get_world_prompt


@register_tool(
    name="get_weather",
    description=(
        "查询指定城市的天气信息（温度、湿度、降水概率、风速等）。"
        "当用户问天气、温度、要不要带伞、穿什么衣服时使用。"
    ),
    parameters=[
        ToolParam(
            name="user_id",
            type="string",
            description="用户ID",
        ),
        ToolParam(
            name="city",
            type="string",
            description="城市名称，例如'北京'、'上海'、'深圳'",
            required=False,
        ),
    ],
)
async def get_weather(user_id: str, city: str = "") -> str:
    """查询天气并返回结果。"""
    # get_world_prompt 内部会根据用户 profile 获取天气
    # 这里传 include_weather=True 强制获取天气
    result = await get_world_prompt(
        user_id=user_id,
        user_text=f"{city}天气" if city else "天气",
        include_weather=True,
    )
    if not result:
        return "天气信息获取失败，请稍后再试。"
    return result
