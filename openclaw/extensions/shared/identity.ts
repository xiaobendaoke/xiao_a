import { env } from "./env.js";

let userAliasMapCache: Map<string, string> | null = null;
let lastAliasRaw = "";

export function getUserAliasMap(): Map<string, string> {
    const raw = (env("XIAO_USER_ALIAS_MAP") || env("XIAO_EMOTION_ALIAS_MAP") || "").trim();
    if (userAliasMapCache && lastAliasRaw === raw) {
        return userAliasMapCache;
    }
    userAliasMapCache = parseUserAliasMap(raw);
    lastAliasRaw = raw;
    return userAliasMapCache;
}

export function inferRecipientId(text: string): string | null {
    const raw = (text || "").trim();
    if (!raw) {
        return null;
    }

    const qqScoped = raw.match(/\bqqbot:(?:c2c|group):([A-Za-z0-9._:-]{6,128})\b/i);
    if (qqScoped?.[1]) {
        return qqScoped[1];
    }

    const longHex = raw.match(/\b[A-Fa-f0-9]{24,64}\b/);
    if (longHex?.[0]) {
        return longHex[0];
    }

    const longDigits = raw.match(/\b\d{7,20}\b/);
    if (longDigits?.[0]) {
        return longDigits[0];
    }

    const parenthesized = raw.match(/\(([A-Za-z0-9._:-]{8,128})\)/);
    if (parenthesized?.[1]) {
        return parenthesized[1];
    }

    return null;
}

export function resolveUserKeyFromPrompt(prompt: string, sessionKey?: string): string {
    const inferred = inferRecipientId(prompt);
    if (inferred) {
        return normalizeUserKey(`qqbot:${inferred}`);
    }
    if (sessionKey) {
        return `session:${sessionKey}`;
    }
    return "session:unknown";
}

export function resolveUserKeyFromOutbound(ctx: { channelId?: string; conversationId?: string }, to: string): string {
    const channel = (ctx.channelId || "unknown").trim();
    const conversation = (ctx.conversationId || "").trim();
    const target = (to || "").trim();
    if (target) {
        return normalizeUserKey(`${channel}:${target}`);
    }
    if (conversation) {
        return normalizeUserKey(`${channel}:${conversation}`);
    }
    return `${channel}:unknown`;
}

export function normalizeUserKey(raw: string): string {
    const text = (raw || "").trim();
    if (!text) {
        return "session:unknown";
    }

    let id = text;
    // If we have double prefixes like qqbot:qqbot:
    const wrappedMatch = text.match(/^(?:qqbot:)+(c2c|group):([A-Za-z0-9._:-]{6,128})$/i);
    if (wrappedMatch && wrappedMatch[2]) {
        id = wrappedMatch[2];
    } else {
        const qqScoped = text.match(/^qqbot:(?:c2c|group):([A-Za-z0-9._:-]{6,128})$/i);
        if (qqScoped && qqScoped[1]) {
            id = qqScoped[1];
        } else if (/^qqbot:/i.test(text)) {
            id = text.slice("qqbot:".length);
        } else if (/^[A-Za-z0-9._:-]{6,128}$/.test(text) && !text.includes(":")) {
            id = text;
        }
    }

    return `qqbot:${normalizeQqIdentity(id)}`;
}

export function normalizeQqIdentity(raw: string): string {
    const text = (raw || "").trim();
    if (/^[A-Fa-f0-9]{24,64}$/.test(text)) {
        return text.toUpperCase();
    }
    return text;
}

export function parseUserAliasMap(raw: string): Map<string, string> {
    const parsed = new Map<string, string>();
    if (!raw) {
        return parsed;
    }

    for (const token of raw.split(/[\n,;]+/)) {
        const part = token.trim();
        if (!part || !part.includes("=")) {
            continue;
        }
        const [leftRaw, rightRaw] = part.split("=", 2);
        const left = normalizeUserKey(leftRaw || "");
        const right = normalizeUserKey(rightRaw || "");
        if (!left || !right || left === right) {
            continue;
        }
        parsed.set(left, right);
    }
    return parsed;
}

export function applyUserAlias(userKey: string): { resolved: string; aliasFrom?: string } {
    let current = normalizeUserKey(userKey);
    const visited = new Set<string>();
    const map = getUserAliasMap();
    const original = current;

    while (map.has(current) && !visited.has(current)) {
        visited.add(current);
        current = map.get(current) || current;
    }

    return {
        resolved: current,
        aliasFrom: current !== original ? original : undefined,
    };
}

// Backward-compatible alias used by migrated modules.
export function applyAlias(userKey: string): { resolved: string; aliasFrom?: string } {
    return applyUserAlias(userKey);
}

export function extractLegacyUserId(userKey: string): string | null {
    const normalized = normalizeUserKey(userKey);
    if (!normalized.startsWith("qqbot:")) {
        return null;
    }
    const id = normalized.slice("qqbot:".length).trim();
    return id || null;
}
