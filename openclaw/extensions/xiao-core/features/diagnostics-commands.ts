import type { OpenClawPluginApi } from "openclaw/plugin-sdk";

type DiagnosticsDeps = {
  formatUptimeSec: () => string;
  sessionUserMapSize: () => number;
  resolveStateFilePath: () => string;
  resolvePersonaPromptFilePath: () => string;
  envStatus: (name: string) => string;
  normalizeUserKey: (raw: string) => string;
  applyAlias: (rawUserKey: string) => { resolved: string; aliasFrom?: string };
  shorten: (text: string, maxLen: number) => string;
};

export function registerXiaoDiagnosticsCommands(api: OpenClawPluginApi, deps: DiagnosticsDeps): void {
  // 注册 /xiao-health 命令，用于展示健康状态与环境信息
  api.registerCommand({
    name: "xiao-health",
    description: "Show OpenClaw QQ migration health snapshot.",
    acceptsArgs: false,
    handler: async (ctx) => {
      // 收集各项诊断信息的字符串数组
      const lines: string[] = [];
      lines.push("xiao-core health");
      
      // 添加时间与运行时长
      lines.push(`- now: ${new Date().toISOString()}`);
      lines.push(`- uptime_sec: ${deps.formatUptimeSec()}`);
      
      // 添加上下文基本信息
      lines.push(`- channel: ${ctx.channel}`);
      lines.push(`- conversation: ${(ctx.conversationId || "").trim() || "(none)"}`);
      lines.push(`- sender: ${(ctx.senderId || "").trim() || "(none)"}`);
      
      // 添加缓存与状态信息
      lines.push(`- session_cache_size: ${deps.sessionUserMapSize()}`);
      lines.push(`- state_file: ${deps.resolveStateFilePath()}`);
      lines.push(`- persona_file: ${deps.resolvePersonaPromptFilePath()}`);
      lines.push("");
      
      // 检查关键环境变量的设置状态
      lines.push("env status:");
      lines.push(`- OPENCLAW_GATEWAY_TOKEN: ${deps.envStatus("OPENCLAW_GATEWAY_TOKEN")}`);
      lines.push(`- XIAO_USER_ALIAS_MAP: ${deps.envStatus("XIAO_USER_ALIAS_MAP")}`);
      lines.push(`- XIAO_PERSONA_PROMPT_FILE: ${deps.envStatus("XIAO_PERSONA_PROMPT_FILE")}`);
      lines.push(`- SILICONFLOW_API_KEY: ${deps.envStatus("SILICONFLOW_API_KEY")}`);
      lines.push(`- DASHSCOPE_API_KEY: ${deps.envStatus("DASHSCOPE_API_KEY")}`);
      lines.push(`- DEEPSEEK_API_KEY: ${deps.envStatus("DEEPSEEK_API_KEY")}`);
      lines.push(`- OPENAI_API_KEY: ${deps.envStatus("OPENAI_API_KEY")}`);
      
      // 返回文本格式的大段诊断信息
      return { text: lines.join("\n") };
    },
  });

  // 注册 /xiao-whoami 命令，用于展示身份解析及别名映射情况
  api.registerCommand({
    name: "xiao-whoami",
    description: "Show raw identity and resolved user_key mapping.",
    acceptsArgs: false,
    handler: async (ctx) => {
      // 提取操作者（优先选取 from，若无则选取 senderId，否则回退到 unknown）
      const actor =
        (ctx.from && String(ctx.from).trim()) ||
        (ctx.senderId && String(ctx.senderId).trim()) ||
        "unknown";
      
      // 拼接并规范化原始用户标识列表
      const raw = `${ctx.channel}:${actor}`;
      const normalized = deps.normalizeUserKey(raw);
      
      // 通过别名机制尝试获取真实映射后的用户信息
      const mapped = deps.applyAlias(normalized);

      // 准备向用户展示的信息流
      const lines: string[] = [];
      lines.push("xiao-core whoami");
      lines.push(`- channel: ${ctx.channel}`);
      lines.push(`- from: ${(ctx.from && String(ctx.from).trim()) || "(none)"}`);
      lines.push(`- senderId: ${(ctx.senderId && String(ctx.senderId).trim()) || "(none)"}`);
      lines.push(`- conversationId: ${(ctx.conversationId || "").trim() || "(none)"}`);
      lines.push(`- raw_user_key: ${raw}`);
      lines.push(`- normalized_user_key: ${normalized}`);
      lines.push(`- resolved_user_key: ${mapped.resolved}`);
      
      // 如果触发了别名，显示来源别名
      if (mapped.aliasFrom) {
        lines.push(`- alias_from: ${mapped.aliasFrom}`);
      }
      return { text: lines.join("\n") };
    },
  });

  // 注册 /xiao-echo 命令，用于消息回显与通讯通路测试
  api.registerCommand({
    name: "xiao-echo",
    description: "Echo text with normalized identity (for QQ channel smoke test).",
    acceptsArgs: true,
    handler: async (ctx) => {
      // 提取和解析用户身份
      const actor =
        (ctx.from && String(ctx.from).trim()) ||
        (ctx.senderId && String(ctx.senderId).trim()) ||
        "unknown";
      const raw = `${ctx.channel}:${actor}`;
      const normalized = deps.normalizeUserKey(raw);
      const mapped = deps.applyAlias(normalized);

      // 提取用户输入的内容
      const rawArgs = (ctx.args || "").trim();
      const text = rawArgs || "(empty)";
      
      // 将内容截断以确保安全，防止恶意的长文本注入
      const safeText = deps.shorten(text, 512);

      // 返回被解析出的核心字段，方便排错
      return {
        text: [
          `echo: ${safeText}`,
          `user_key: ${mapped.resolved}`,
          `channel: ${ctx.channel}`,
        ].join("\n"),
      };
    },
  });
}
