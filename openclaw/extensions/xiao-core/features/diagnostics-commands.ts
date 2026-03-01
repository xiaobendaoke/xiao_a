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
  api.registerCommand({
    name: "xiao-health",
    description: "Show OpenClaw QQ migration health snapshot.",
    acceptsArgs: false,
    handler: async (ctx) => {
      const lines: string[] = [];
      lines.push("xiao-core health");
      lines.push(`- now: ${new Date().toISOString()}`);
      lines.push(`- uptime_sec: ${deps.formatUptimeSec()}`);
      lines.push(`- channel: ${ctx.channel}`);
      lines.push(`- conversation: ${(ctx.conversationId || "").trim() || "(none)"}`);
      lines.push(`- sender: ${(ctx.senderId || "").trim() || "(none)"}`);
      lines.push(`- session_cache_size: ${deps.sessionUserMapSize()}`);
      lines.push(`- state_file: ${deps.resolveStateFilePath()}`);
      lines.push(`- persona_file: ${deps.resolvePersonaPromptFilePath()}`);
      lines.push("");
      lines.push("env status:");
      lines.push(`- OPENCLAW_GATEWAY_TOKEN: ${deps.envStatus("OPENCLAW_GATEWAY_TOKEN")}`);
      lines.push(`- XIAO_USER_ALIAS_MAP: ${deps.envStatus("XIAO_USER_ALIAS_MAP")}`);
      lines.push(`- XIAO_PERSONA_PROMPT_FILE: ${deps.envStatus("XIAO_PERSONA_PROMPT_FILE")}`);
      lines.push(`- SILICONFLOW_API_KEY: ${deps.envStatus("SILICONFLOW_API_KEY")}`);
      lines.push(`- DASHSCOPE_API_KEY: ${deps.envStatus("DASHSCOPE_API_KEY")}`);
      lines.push(`- DEEPSEEK_API_KEY: ${deps.envStatus("DEEPSEEK_API_KEY")}`);
      lines.push(`- OPENAI_API_KEY: ${deps.envStatus("OPENAI_API_KEY")}`);
      return { text: lines.join("\n") };
    },
  });

  api.registerCommand({
    name: "xiao-whoami",
    description: "Show raw identity and resolved user_key mapping.",
    acceptsArgs: false,
    handler: async (ctx) => {
      const actor =
        (ctx.from && String(ctx.from).trim()) ||
        (ctx.senderId && String(ctx.senderId).trim()) ||
        "unknown";
      const raw = `${ctx.channel}:${actor}`;
      const normalized = deps.normalizeUserKey(raw);
      const mapped = deps.applyAlias(normalized);

      const lines: string[] = [];
      lines.push("xiao-core whoami");
      lines.push(`- channel: ${ctx.channel}`);
      lines.push(`- from: ${(ctx.from && String(ctx.from).trim()) || "(none)"}`);
      lines.push(`- senderId: ${(ctx.senderId && String(ctx.senderId).trim()) || "(none)"}`);
      lines.push(`- conversationId: ${(ctx.conversationId || "").trim() || "(none)"}`);
      lines.push(`- raw_user_key: ${raw}`);
      lines.push(`- normalized_user_key: ${normalized}`);
      lines.push(`- resolved_user_key: ${mapped.resolved}`);
      if (mapped.aliasFrom) {
        lines.push(`- alias_from: ${mapped.aliasFrom}`);
      }
      return { text: lines.join("\n") };
    },
  });

  api.registerCommand({
    name: "xiao-echo",
    description: "Echo text with normalized identity (for QQ channel smoke test).",
    acceptsArgs: true,
    handler: async (ctx) => {
      const actor =
        (ctx.from && String(ctx.from).trim()) ||
        (ctx.senderId && String(ctx.senderId).trim()) ||
        "unknown";
      const raw = `${ctx.channel}:${actor}`;
      const normalized = deps.normalizeUserKey(raw);
      const mapped = deps.applyAlias(normalized);

      const rawArgs = (ctx.args || "").trim();
      const text = rawArgs || "(empty)";
      const safeText = deps.shorten(text, 512);

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
