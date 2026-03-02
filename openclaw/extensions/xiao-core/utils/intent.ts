import { shorten } from "../../shared/text.js";
import {
    inferRecipientId as inferRecipientIdShared,
    normalizeUserKey,
    resolveUserKeyFromPrompt as resolveUserKeyFromPromptShared,
} from "../../shared/identity.js";
import { clamp } from "../../shared/text.js";

export function extractUserInput(prompt: string): string {
    const src = (prompt || "").trim();
    if (!src) {
        return "";
    }

    const patterns = [
        /(?:^|\n)(?:用户输入|用户|User|USER|message|Message)\s*[：:]\s*(.+)$/gim,
        /(?:^|\n)(?:任务|问题|query)\s*[：:]\s*(.+)$/gim,
    ];

    for (const p of patterns) {
        let m: RegExpExecArray | null = null;
        let last: RegExpExecArray | null = null;
        while ((m = p.exec(src)) !== null) {
            last = m;
        }
        const candidate = (last?.[1] || "").trim();
        if (candidate) {
            return shorten(candidate, 800);
        }
    }

    const lines = src
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter((line) => !!line);
    if (lines.length === 0) {
        return "";
    }
    return shorten(lines[lines.length - 1] || "", 800);
}

export function inferRecipientId(text: string): string | null {
    return inferRecipientIdShared(text);
}

export function resolveUserKeyFromPrompt(prompt: string, sessionKey?: string): string {
    return resolveUserKeyFromPromptShared(prompt, sessionKey);
}

export function extractExplicitMemory(input: string): string | null {
    const text = (input || "").trim();
    if (!text) {
        return null;
    }

    const prefixes = ["记住：", "记住:", "请记住：", "请记住:", "备忘：", "备忘:"];
    for (const prefix of prefixes) {
        if (text.startsWith(prefix)) {
            const payload = text.slice(prefix.length).trim();
            return payload ? shorten(payload, 240) : null;
        }
    }
    return null;
}

export function hasWeatherIntent(input: string): boolean {
    const t = (input || "").toLowerCase();
    return ["天气", "气温", "下雨", "降雨", "温度", "weather", "forecast"].some((k) => t.includes(k));
}

export function hasStockIntent(input: string): boolean {
    const t = (input || "").toLowerCase();
    return ["查股", "股票", "股价", "a股", "港股", "美股", "stock", "ticker"].some((k) => t.includes(k));
}

export function hasGithubTrendingIntent(input: string): boolean {
    const t = (input || "").toLowerCase();
    return ["github周榜", "github 热榜", "github trending", "trending", "开源周榜"].some((k) => t.includes(k));
}

export function isLikelyAttachmentOnlyInput(input: string): boolean {
    const t = (input || "").trim();
    if (!t) return true;
    if (t.length <= 8 && /(图片|语音|附件)/.test(t)) {
        return true;
    }
    const markers = [
        "用户发送了一张图片",
        "用户发送了一条语音消息",
        "请不要凭空描述图片内容",
        "回答前必须先调用",
        "图片地址",
        "语音文件",
        "发送时间",
    ];
    return markers.some((m) => t.includes(m));
}

export function hasUrlSummaryIntent(input: string): boolean {
    const t = (input || "").toLowerCase();
    if (!t) return false;
    const keys = [
        "总结",
        "概括",
        "提炼",
        "看下",
        "解读",
        "这链接讲了啥",
        "这篇讲了啥",
        "summarize",
        "summary",
    ];
    return keys.some((k) => t.includes(k));
}

export function hasSourceFollowupIntent(input: string): boolean {
    const t = (input || "").toLowerCase();
    if (!t) return false;
    const keys = [
        "来源",
        "链接",
        "出处",
        "原文",
        "参考",
        "发我链接",
        "给我链接",
        "source",
        "link",
    ];
    return keys.some((k) => t.includes(k));
}

export function parseReminderArgs(raw: string): { minutes: number; content: string } | null {
    const text = (raw || "").trim();
    if (!text) {
        return null;
    }

    const patterns = [
        /^(\d{1,5})\s+(.+)$/,
        /^(\d{1,5})m(?:in)?\s+(.+)$/i,
        /^(\d{1,5})\s*(?:分钟|分)\s*(?:后)?\s+(.+)$/i,
    ];

    for (const p of patterns) {
        const m = text.match(p);
        if (!m?.[1] || !m?.[2]) {
            continue;
        }
        const minutes = clamp(Number(m[1]), 1, 43200);
        const content = shorten(m[2], 240).trim();
        if (!content) {
            return null;
        }
        return { minutes, content };
    }
    return null;
}

export function parseReminderIntent(input: string): { minutes: number; content: string } | null {
    const text = (input || "").trim();
    if (!text) {
        return null;
    }

    const minDirect = text.match(/提醒我\s*(\d{1,4})\s*(分钟|分|min)\s*后?\s*(.+)$/i);
    if (minDirect?.[1] && minDirect?.[3]) {
        return {
            minutes: clamp(Number(minDirect[1]), 1, 43200),
            content: shorten(minDirect[3], 240),
        };
    }

    const hourDirect = text.match(/提醒我\s*(\d{1,3})\s*(小时|时|h|hour)\s*后?\s*(.+)$/i);
    if (hourDirect?.[1] && hourDirect?.[3]) {
        return {
            minutes: clamp(Number(hourDirect[1]) * 60, 1, 43200),
            content: shorten(hourDirect[3], 240),
        };
    }

    const minInvert = text.match(/(\d{1,4})\s*(分钟|分|min)\s*后?\s*提醒我\s*(.+)$/i);
    if (minInvert?.[1] && minInvert?.[3]) {
        return {
            minutes: clamp(Number(minInvert[1]), 1, 43200),
            content: shorten(minInvert[3], 240),
        };
    }

    return null;
}

export function reminderTargetFromUserKey(userKey: string): string | null {
    const m = (userKey || "").trim().match(/^qqbot:([A-Za-z0-9._:-]{6,128})$/);
    if (!m?.[1]) {
        return null;
    }
    return `qqbot:c2c:${m[1]}`;
}

export function extractJsonPayload(raw: string): unknown {
    const text = (raw || "").trim();
    if (!text) {
        return {};
    }

    const fromBrace = text.slice(Math.max(0, text.indexOf("{"))).trim();
    if (fromBrace.startsWith("{")) {
        try {
            return JSON.parse(fromBrace);
        } catch {
            // no-op
        }
    }

    const fromBracket = text.slice(Math.max(0, text.indexOf("["))).trim();
    if (fromBracket.startsWith("[")) {
        try {
            return JSON.parse(fromBracket);
        } catch {
            // no-op
        }
    }

    return { raw: text.slice(0, 600) };
}

export function resolveQqTargetFromCtx(ctx: {
    channel?: string;
    from?: string;
    senderId?: string;
    conversationId?: string;
}): string | null {
    if ((ctx.channel || "").trim() !== "qqbot") {
        return null;
    }

    const conv = (ctx.conversationId || "").trim();
    const scoped = conv.match(/qqbot:(c2c|group):([A-Za-z0-9._:-]{6,128})/i);
    if (scoped?.[1] && scoped?.[2]) {
        return `qqbot:${scoped[1]}:${scoped[2]}`;
    }

    const fallback =
        (ctx.from && String(ctx.from).trim()) || (ctx.senderId && String(ctx.senderId).trim()) || "";
    if (fallback) {
        const fallbackScope = fallback.match(/^(c2c|group):([A-Za-z0-9._:-]{6,128})$/i);
        if (fallbackScope?.[1] && fallbackScope?.[2]) {
            return `qqbot:${fallbackScope[1]}:${fallbackScope[2]}`;
        }
        return `qqbot:c2c:${fallback}`;
    }

    return null;
}
