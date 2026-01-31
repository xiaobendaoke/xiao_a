"""Skills 加载器。

负责：
- 扫描 skills/ 目录下的所有 .skill.md 文件
- 解析 YAML frontmatter 和正文内容
- 提供 get_skill() / list_skills() 接口
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from nonebot import logger


@dataclass
class DataSource:
    """Skill 数据源配置"""
    name: str
    function: str
    args: dict = field(default_factory=dict)


@dataclass
class Skill:
    """Skill 定义"""
    name: str
    description: str
    triggers_prompt: str  # 用于 LLM 路由判断的描述
    data_sources: list[DataSource]
    content: str  # Markdown 正文（角色定义、分析框架等）
    file_path: str = ""


# 缓存已加载的 skills
_skills_cache: dict[str, Skill] = {}
_skills_loaded: bool = False


def _parse_skill_file(file_path: Path) -> Skill | None:
    """解析单个 .skill.md 文件"""
    try:
        text = file_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"[skills] failed to read {file_path}: {e}")
        return None

    # 解析 YAML frontmatter（--- 开头和结尾）
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.S)
    if not m:
        logger.warning(f"[skills] no frontmatter in {file_path}")
        return None

    frontmatter_str, content = m.group(1), m.group(2)

    try:
        frontmatter = yaml.safe_load(frontmatter_str) or {}
    except Exception as e:
        logger.warning(f"[skills] failed to parse frontmatter in {file_path}: {e}")
        return None

    name = frontmatter.get("name", "")
    if not name:
        logger.warning(f"[skills] missing 'name' in {file_path}")
        return None

    # 解析 data_sources
    data_sources = []
    for ds in frontmatter.get("data_sources", []):
        if isinstance(ds, dict) and ds.get("function"):
            data_sources.append(DataSource(
                name=ds.get("name", ds["function"]),
                function=ds["function"],
                args=ds.get("args", {}),
            ))

    return Skill(
        name=name,
        description=frontmatter.get("description", ""),
        triggers_prompt=frontmatter.get("triggers_prompt", ""),
        data_sources=data_sources,
        content=content.strip(),
        file_path=str(file_path),
    )


def _load_all_skills() -> None:
    """加载所有 skills"""
    global _skills_cache, _skills_loaded
    if _skills_loaded:
        return

    skills_dir = Path(__file__).parent
    for file_path in skills_dir.glob("*.skill.md"):
        skill = _parse_skill_file(file_path)
        if skill:
            _skills_cache[skill.name] = skill
            logger.info(f"[skills] loaded: {skill.name} ({file_path.name})")

    _skills_loaded = True
    logger.info(f"[skills] total loaded: {len(_skills_cache)}")


def get_skill(name: str) -> Skill | None:
    """获取指定 skill"""
    _load_all_skills()
    return _skills_cache.get(name)


def list_skills() -> list[Skill]:
    """列出所有可用 skills"""
    _load_all_skills()
    return list(_skills_cache.values())


def get_skills_summary() -> str:
    """生成 skills 摘要（用于 router prompt）"""
    _load_all_skills()
    lines = []
    for skill in _skills_cache.values():
        lines.append(f"- {skill.name}: {skill.triggers_prompt}")
    return "\n".join(lines) if lines else "（暂无可用模块）"


def reload_skills() -> None:
    """重新加载所有 skills（用于热更新）"""
    global _skills_cache, _skills_loaded
    _skills_cache.clear()
    _skills_loaded = False
    _load_all_skills()
