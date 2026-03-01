import type { OpenClawPluginApi } from "openclaw/plugin-sdk";

type ReminderArgs = {
  minutes: number;
  content: string;
};

type ReminderContextLike = {
  channel: string;
  from?: string;
  senderId?: string;
  conversationId?: string;
};

type RemindCommandDeps = {
  parseReminderArgs: (text: string) => ReminderArgs | null;
  resolveQqTargetFromCtx: (ctx: {
    channel: string;
    from: string;
    senderId: string;
    conversationId: string;
  }) => string;
  execFileAsync: (
    file: string,
    args: string[],
    options: { timeout: number; maxBuffer: number },
  ) => Promise<{ stdout?: string | Buffer }>;
  extractJsonPayload: (text: string) => unknown;
  shorten: (text: string, maxLen: number) => string;
};

export function registerXiaoRemindCommand(api: OpenClawPluginApi, deps: RemindCommandDeps): void {
  api.registerCommand({
    name: "xiao-remind",
    description: "Create one-shot reminder. Usage: /xiao-remind <minutes> <content>",
    acceptsArgs: true,
    handler: async (ctx: ReminderContextLike & { args?: string }) => {
      const parsed = deps.parseReminderArgs((ctx.args || "").trim());
      if (!parsed) {
        return {
          text: "usage: /xiao-remind <minutes> <content>\nexample: /xiao-remind 30 记得喝水",
        };
      }

      const to = deps.resolveQqTargetFromCtx({
        channel: ctx.channel,
        from: (ctx.from && String(ctx.from)) || "",
        senderId: (ctx.senderId && String(ctx.senderId)) || "",
        conversationId: ctx.conversationId || "",
      });
      if (!to) {
        return {
          text: "当前上下文不是 qqbot，无法自动识别提醒目标。请在 QQ 私聊使用此命令。",
        };
      }

      const name = `xiao-reminder-${Date.now()}-${Math.trunc(Math.random() * 1000)}`;
      const message = `你是小a。提醒内容：${parsed.content}`;
      const args = [
        "cron",
        "add",
        "--name",
        name,
        "--at",
        `${parsed.minutes}m`,
        "--message",
        message,
        "--announce",
        "--channel",
        "qqbot",
        "--to",
        to,
        "--session",
        "isolated",
        "--delete-after-run",
        "--json",
      ];

      try {
        const { stdout } = await deps.execFileAsync("openclaw", args, {
          timeout: 25000,
          maxBuffer: 1024 * 1024,
        });
        const parsedOut = deps.extractJsonPayload(String(stdout || ""));
        const out = parsedOut as Record<string, unknown>;
        const jobId = String(out.id || "").trim() || "(unknown)";
        return {
          text: [
            "提醒已创建",
            `- to: ${to}`,
            `- after: ${parsed.minutes}m`,
            `- content: ${parsed.content}`,
            `- job_id: ${jobId}`,
          ].join("\n"),
        };
      } catch (err) {
        const e = err as Error & { stderr?: string; stdout?: string };
        const msg =
          `${(e.stderr || "").trim()} ${(e.stdout || "").trim()}`.trim() ||
          (e.message || "failed to create reminder");
        return { text: `提醒创建失败：${deps.shorten(msg, 280)}` };
      }
    },
  });
}
