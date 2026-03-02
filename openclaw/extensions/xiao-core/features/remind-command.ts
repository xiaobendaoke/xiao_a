import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { shorten } from "../../shared/text.js";
import { normalizeQqIdentity } from "../../shared/identity.js";

const execFileAsync = promisify(execFile);

export function parseReminderArgs(text: string): { minutes: number; content: string } | null {
  const m = (text || "").trim().match(/^(\d+(?:\.\d+)?)\s+(.+)$/);
  if (!m) return null;
  const minutes = Number(m[1]);
  const content = (m[2] || "").trim();
  if (!Number.isFinite(minutes) || minutes <= 0 || !content) {
    return null;
  }
  return { minutes, content };
}

export function parseReminderIntent(input: string): { minutes: number; content: string } | null {
  const t = (input || "").trim();
  const m = t.match(/^(?:帮我|让我)?(\d+(?:\.\d+)?)(?:个|分钟)?(?:小时)?(?:之后|以后(?:提醒我)?)(.+)$/);
  if (!m) return null;

  let num = Number(m[1]);
  if (!Number.isFinite(num)) return null;

  if (t.includes("小时")) {
    num *= 60;
  }
  const content = (m[2] || "").replace(/^(的?时候|再的?)|(告诉我|提醒我)/g, "").trim();
  if (num <= 0 || !content) return null;
  return { minutes: num, content };
}

export function reminderTargetFromUserKey(userKey: string): string | null {
  const normalized = (userKey || "").trim();
  if (!normalized || normalized === "session:unknown") return null;

  const mGroup = normalized.match(/^qqbot:(group:[A-Za-z0-9._:-]+)$/);
  if (mGroup) {
    return mGroup[1] || null;
  }

  const mUser = normalized.match(/^qqbot:(?:c2c:)?([A-Za-z0-9._:-]+)$/);
  if (mUser) {
    return `user:${normalizeQqIdentity(mUser[1] || "")}`;
  }

  return null;
}

export function resolveQqTargetFromCtx(ctx: {
  channel: string;
  from: string;
  senderId: string;
  conversationId: string;
}): string | null {
  if (ctx.channel !== "qqbot") return null;

  const conv = (ctx.conversationId || "").trim();
  if (conv.startsWith("group:")) {
    return conv;
  }

  const u = (ctx.from || ctx.senderId || "").trim();
  if (u) {
    return `user:${normalizeQqIdentity(u)}`;
  }
  return null;
}

export function extractJsonPayload(text: string): unknown {
  const t = (text || "").trim();
  if (!t) return {};
  try {
    return JSON.parse(t);
  } catch {
    const m = t.match(/\{[\s\S]*\}|\[[\s\S]*\]/);
    if (m) {
      try {
        return JSON.parse(m[0]);
      } catch {
        return {};
      }
    }
  }
  return {};
}

type ReminderContextLike = {
  channel: string;
  from?: string;
  senderId?: string;
  conversationId?: string;
};

export function registerXiaoRemindCommand(api: OpenClawPluginApi): void {
  // 注册 /xiao-remind 命令，用于给指定对象创建一次性定时延期提醒
  api.registerCommand({
    name: "xiao-remind",
    description: "Create one-shot reminder. Usage: /xiao-remind <minutes> <content>",
    acceptsArgs: true,
    handler: async (ctx: ReminderContextLike & { args?: string }) => {
      // 从输入中抽取出提醒的倒计时（分钟）以及要提醒的内容
      const parsed = parseReminderArgs((ctx.args || "").trim());
      if (!parsed) {
        return {
          text: "usage: /xiao-remind <minutes> <content>\nexample: /xiao-remind 30 记得喝水",
        };
      }

      // 获取当前要提醒的对象 QQ 或者频道目标
      const to = resolveQqTargetFromCtx({
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

      // 构造独立的后台 cron 定时任务参数
      const name = `xiao-reminder-${Date.now()}-${Math.trunc(Math.random() * 1000)}`;
      const message = `你是小a。提醒内容：${parsed.content}`;

      // 使用 CLI 接口传递给 openclaw 工具的命令数组
      const args = [
        "cron",
        "add",
        "--name",
        name,
        "--at",
        `${parsed.minutes}m`, // 执行时间设定
        "--message",
        message, // 定时提醒抛出时的大致系统提示
        "--announce",
        "--channel",
        "qqbot", // 指定分发频道
        "--to",
        to, // 指定接收者
        "--session",
        "isolated",
        "--delete-after-run", // 运行结束立马删除自身，实现一次性调度
        "--json",
      ];

      try {
        // 通过子进程异步执行后台注册任务
        const { stdout } = await execFileAsync("openclaw", args, {
          timeout: 25000,
          maxBuffer: 1024 * 1024,
        });

        // 解析标准输出中包含的 JSON 数据判断是否返回成功记录的 job ID
        const parsedOut = extractJsonPayload(String(stdout || ""));
        const out = parsedOut as Record<string, unknown>;
        const jobId = String(out.id || "").trim() || "(unknown)";

        // 创建成功
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
        // 创建任务报错处理
        const e = err as Error & { stderr?: string; stdout?: string };
        const msg =
          `${(e.stderr || "").trim()} ${(e.stdout || "").trim()}`.trim() ||
          (e.message || "failed to create reminder");
        return { text: `提醒创建失败：${shorten(msg, 280)}` };
      }
    },
  });
}
