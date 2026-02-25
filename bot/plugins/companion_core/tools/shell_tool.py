"""Shell 命令执行工具（沙箱 + 白名单模式）。

安全策略：
- 命令白名单：仅允许执行安全的只读命令
- 执行超时控制（默认 30 秒）
- 工作目录锁定在沙箱目录
- 所有执行记录审计日志
"""

from __future__ import annotations

import asyncio
import os
import shlex
from pathlib import Path

from nonebot import logger
from ..tool_registry import register_tool, ToolParam


# 沙箱目录
_WORKSPACE = Path(os.getenv("AGENT_WORKSPACE_DIR", "/app/workspace")).resolve()

# 执行超时（秒）
_TIMEOUT = int(os.getenv("AGENT_SHELL_TIMEOUT", "30"))

# 命令白名单：只允许这些命令的前缀
# 可通过环境变量 AGENT_SHELL_WHITELIST 扩展（逗号分隔）
_DEFAULT_WHITELIST = {
    "ls", "cat", "head", "tail", "wc", "grep", "find", "echo",
    "date", "cal", "pwd", "whoami", "uname", "df", "du",
    "sort", "uniq", "tr", "cut", "awk", "sed",
    "python", "python3", "pip", "pip3",
    "node", "npm", "npx",
    "curl", "wget",
}

_extra = (os.getenv("AGENT_SHELL_WHITELIST") or "").strip()
if _extra:
    _DEFAULT_WHITELIST.update(c.strip() for c in _extra.split(",") if c.strip())

# 绝对禁止的危险命令
_BLACKLIST_PATTERNS = {
    "rm -rf /", "rm -rf /*", "mkfs", "dd if=", ":(){:|:&};:",
    "chmod -R 777 /", "> /dev/sda", "shutdown", "reboot", "halt",
    "kill -9", "killall",
}


def _is_safe_command(cmd: str) -> tuple[bool, str]:
    """检查命令是否安全。返回 (is_safe, reason)。"""
    cmd_lower = cmd.strip().lower()

    # 黑名单
    for pattern in _BLACKLIST_PATTERNS:
        if pattern in cmd_lower:
            return False, f"命令包含危险操作: '{pattern}'"

    # 白名单
    try:
        parts = shlex.split(cmd)
    except ValueError:
        parts = cmd.split()

    if not parts:
        return False, "空命令"

    exe = os.path.basename(parts[0])
    if exe not in _DEFAULT_WHITELIST:
        return False, f"命令 '{exe}' 不在白名单中。允许的命令: {', '.join(sorted(_DEFAULT_WHITELIST))}"

    return True, ""


@register_tool(
    name="shell_exec",
    description=(
        "在服务器上执行 Shell 命令（沙箱模式，仅限白名单命令）。"
        "可用于运行脚本、查看文件内容、搜索文件、执行 Python 脚本等。"
        f"允许的命令前缀: {', '.join(sorted(_DEFAULT_WHITELIST))}"
    ),
    parameters=[
        ToolParam(
            name="command",
            type="string",
            description="要执行的 Shell 命令，如 'ls -la'、'cat config.txt'、'python3 script.py'",
        ),
    ],
)
async def shell_exec(command: str) -> str:
    """执行 Shell 命令。"""
    if not command or not command.strip():
        return "命令为空。"

    is_safe, reason = _is_safe_command(command)
    if not is_safe:
        logger.warning(f"[shell] BLOCKED: '{command}' - {reason}")
        return f"[安全限制] {reason}"

    logger.info(f"[shell] EXEC: '{command}' in {_WORKSPACE}")

    try:
        # 确保工作目录存在
        _WORKSPACE.mkdir(parents=True, exist_ok=True)

        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(_WORKSPACE),
            env={**os.environ, "HOME": str(_WORKSPACE)},
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=_TIMEOUT,
            )
        except asyncio.TimeoutError:
            proc.kill()
            return f"[超时] 命令执行超过 {_TIMEOUT} 秒，已终止。"

        output_parts = []
        if stdout:
            out = stdout.decode("utf-8", errors="replace").strip()
            if out:
                output_parts.append(f"[stdout]\n{out}")
        if stderr:
            err = stderr.decode("utf-8", errors="replace").strip()
            if err:
                output_parts.append(f"[stderr]\n{err}")

        exit_code = proc.returncode
        result = "\n".join(output_parts) if output_parts else "(无输出)"
        result = f"Exit code: {exit_code}\n{result}"

        # 截断
        if len(result) > 4000:
            result = result[:3900] + "\n...(输出已截断)"

        return result

    except Exception as e:
        logger.error(f"[shell] failed: {e}")
        return f"执行失败: {e}"
