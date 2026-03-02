import { promises as fs, existsSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { normalizeUserKey } from "../../shared/identity.js";
import { shorten } from "../../shared/text.js";
import { clamp } from "../../shared/text.js";

export const SESSION_TTL_MS = 6 * 60 * 60 * 1000;
export const SESSION_MAX_SIZE = 2000;

export const MAX_NOTE_LEN = 300;
export const MAX_NOTES_PER_USER = 200;
export const MAX_CHAT_LEN = 400;
export const MAX_CHATS_PER_USER = 240;
export const MAX_LINKS_PER_USER = 80;
export const MAX_MEMOS_PER_USER = 200;
export const MAX_MEMO_LEN = 360;

export type SessionSnapshot = {
    resolvedUserKey: string;
    aliasFrom?: string;
    seenAt: number;
    promptPreview: string;
    userInput?: string;
    userInputRecorded?: boolean;
};

export type MemoryNote = {
    text: string;
    ts: number;
    source: "explicit" | "derived";
};

export type ChatEntry = {
    role: "user" | "assistant";
    text: string;
    ts: number;
};

export type MemoEntry = {
    id: string;
    text: string;
    tags: string[];
    ts: number;
};

export type GithubWeeklyMark = {
    weekKey: string;
    ts: number;
};

export type LinkEvidence = {
    url: string;
    ts: number;
    source: "user" | "assistant";
    context: string;
};

export type RagHit = {
    score: number;
    ts: number;
    text: string;
    from: "note" | "chat";
};

export type CoreState = {
    notes: Record<string, MemoryNote[]>;
    chats: Record<string, ChatEntry[]>;
    links: Record<string, LinkEvidence[]>;
    memos: Record<string, MemoEntry[]>;
    githubWeekly: Record<string, GithubWeeklyMark>;
};

export const STARTED_AT = Date.now();
export const SESSION_USER_MAP = new Map<string, SessionSnapshot>();

export const DEFAULT_CORE_STATE: CoreState = {
    notes: {},
    chats: {},
    links: {},
    memos: {},
    githubWeekly: {},
};

let stateCache: CoreState | null = null;
let stateWriteQueue: Promise<void> = Promise.resolve();

export function resolveStateFilePath(): string {
    const fromEnv = (process.env.XIAO_CORE_STATE_FILE || "").trim();
    if (fromEnv) {
        return fromEnv;
    }
    return path.join(homedir(), ".openclaw", "xiao-core", "state.json");
}

export function formatUptimeSec(): number {
    return Math.floor((Date.now() - STARTED_AT) / 1000);
}

export function sweepSessionCache(now: number): void {
    for (const [k, v] of SESSION_USER_MAP.entries()) {
        if (now - v.seenAt > SESSION_TTL_MS) {
            SESSION_USER_MAP.delete(k);
        }
    }
    if (SESSION_USER_MAP.size <= SESSION_MAX_SIZE) {
        return;
    }

    const sorted = [...SESSION_USER_MAP.entries()].sort((a, b) => a[1].seenAt - b[1].seenAt);
    const overflow = SESSION_USER_MAP.size - SESSION_MAX_SIZE;
    for (let i = 0; i < overflow; i += 1) {
        const key = sorted[i]?.[0];
        if (key) {
            SESSION_USER_MAP.delete(key);
        }
    }
}

export async function ensureStateLoaded(): Promise<CoreState> {
    if (stateCache) {
        return stateCache;
    }

    const stateFile = resolveStateFilePath();
    try {
        const raw = await fs.readFile(stateFile, "utf8");
        const parsed = JSON.parse(raw) as Partial<CoreState>;
        stateCache = {
            notes: parsed.notes && typeof parsed.notes === "object" ? parsed.notes : {},
            chats: parsed.chats && typeof parsed.chats === "object" ? parsed.chats : {},
            links: parsed.links && typeof parsed.links === "object" ? parsed.links : {},
            memos: parsed.memos && typeof parsed.memos === "object" ? parsed.memos : {},
            githubWeekly: parsed.githubWeekly && typeof parsed.githubWeekly === "object" ? parsed.githubWeekly : {},
        };
        return stateCache;
    } catch {
        stateCache = {
            notes: {},
            chats: {},
            links: {},
            memos: {},
            githubWeekly: {},
        };
        return stateCache;
    }
}

export async function persistState(): Promise<void> {
    const stateFile = resolveStateFilePath();
    const dir = path.dirname(stateFile);

    stateWriteQueue = stateWriteQueue.then(async () => {
        await fs.mkdir(dir, { recursive: true });
        const payload = stateCache || DEFAULT_CORE_STATE;
        await fs.writeFile(stateFile, JSON.stringify(payload, null, 2), "utf8");
    });

    await stateWriteQueue;
}

export async function addMemoryNote(userKey: string, text: string, source: "explicit" | "derived"): Promise<void> {
    const normalized = normalizeUserKey(userKey);
    if (!normalized || !text.trim()) {
        return;
    }

    const store = await ensureStateLoaded();
    const arr = store.notes[normalized] || [];
    arr.push({
        text: shorten(text, MAX_NOTE_LEN),
        ts: Date.now(),
        source,
    });

    if (arr.length > MAX_NOTES_PER_USER) {
        arr.splice(0, arr.length - MAX_NOTES_PER_USER);
    }
    store.notes[normalized] = arr;
    await persistState();
}

export async function addChatEntry(userKey: string, role: "user" | "assistant", text: string): Promise<void> {
    const normalized = normalizeUserKey(userKey);
    const clean = shorten(text, MAX_CHAT_LEN).trim();
    if (!normalized || !clean) {
        return;
    }

    const store = await ensureStateLoaded();
    const arr = store.chats[normalized] || [];
    arr.push({
        role,
        text: clean,
        ts: Date.now(),
    });

    if (arr.length > MAX_CHATS_PER_USER) {
        arr.splice(0, arr.length - MAX_CHATS_PER_USER);
    }
    store.chats[normalized] = arr;
    await persistState();
}

export async function getRecentChats(userKey: string, limit: number): Promise<ChatEntry[]> {
    const store = await ensureStateLoaded();
    const arr = (store.chats[normalizeUserKey(userKey)] || []).slice();
    return arr.slice(Math.max(0, arr.length - limit));
}

export async function getRecentNotes(userKey: string, limit: number): Promise<MemoryNote[]> {
    const store = await ensureStateLoaded();
    const arr = (store.notes[normalizeUserKey(userKey)] || []).slice();
    return arr.slice(Math.max(0, arr.length - limit));
}

export async function addLinkEvidence(
    userKey: string,
    source: "user" | "assistant",
    url: string,
    context: string,
): Promise<void> {
    const normalizedUser = normalizeUserKey(userKey);
    const normalizedUrl = (url || "").trim(); // we omit normalizeEvidenceUrl here for simplicity or rely on it passed correctly
    if (!normalizedUser || !normalizedUrl) {
        return;
    }
    const store = await ensureStateLoaded();
    const arr = store.links[normalizedUser] || [];
    const now = Date.now();

    const dedup = arr.filter((x) => x.url !== normalizedUrl);
    dedup.push({
        url: normalizedUrl,
        ts: now,
        source,
        context: shorten(context || "", 180),
    });
    if (dedup.length > MAX_LINKS_PER_USER) {
        dedup.splice(0, dedup.length - MAX_LINKS_PER_USER);
    }
    store.links[normalizedUser] = dedup;
    await persistState();
}

export async function getRecentLinks(userKey: string, limit: number): Promise<LinkEvidence[]> {
    const store = await ensureStateLoaded();
    const arr = (store.links[normalizeUserKey(userKey)] || []).slice();
    arr.sort((a, b) => Number(a.ts || 0) - Number(b.ts || 0));
    return arr.slice(Math.max(0, arr.length - clamp(limit, 1, 12)));
}

function makeMemoId(): string {
    return `${Date.now().toString(36)}${Math.trunc(Math.random() * 1e6).toString(36)}`;
}

function parseMemoTags(text: string): string[] {
    const tags = (text.match(/#([^\s#]{1,32})/g) || [])
        .map((x) => x.replace(/^#/, "").trim().toLowerCase())
        .filter(Boolean);
    return [...new Set(tags)].slice(0, 8);
}

export async function addMemoEntry(userKey: string, text: string): Promise<MemoEntry | null> {
    const normalized = normalizeUserKey(userKey);
    const clean = shorten((text || "").trim(), MAX_MEMO_LEN);
    if (!normalized || !clean) return null;
    const store = await ensureStateLoaded();
    const arr = store.memos[normalized] || [];
    const entry: MemoEntry = {
        id: makeMemoId(),
        text: clean,
        tags: parseMemoTags(clean),
        ts: Date.now(),
    };
    arr.push(entry);
    if (arr.length > MAX_MEMOS_PER_USER) {
        arr.splice(0, arr.length - MAX_MEMOS_PER_USER);
    }
    store.memos[normalized] = arr;
    await persistState();
    return entry;
}

export async function getRecentMemos(userKey: string, limit: number): Promise<MemoEntry[]> {
    const store = await ensureStateLoaded();
    const arr = (store.memos[normalizeUserKey(userKey)] || []).slice();
    arr.sort((a, b) => Number(a.ts || 0) - Number(b.ts || 0));
    return arr.slice(Math.max(0, arr.length - clamp(limit, 1, 30)));
}

function tokenize(text: string): string[] {
    const t = (text || "").toLowerCase();
    const raw = t.match(/[\p{Script=Han}A-Za-z0-9_]{1,}/gu) || [];
    const tokens = raw
        .map((s) => s.trim())
        .filter((s) => s.length > 1)
        .slice(0, 128);
    return [...new Set(tokens)];
}

function overlapScore(aTokens: string[], bTokens: string[]): number {
    if (aTokens.length === 0 || bTokens.length === 0) {
        return 0;
    }
    const bSet = new Set(bTokens);
    let hit = 0;
    for (const t of aTokens) {
        if (bSet.has(t)) {
            hit += 1;
        }
    }
    return hit;
}

export async function searchMemos(userKey: string, query: string, limit: number): Promise<MemoEntry[]> {
    const normalized = normalizeUserKey(userKey);
    const q = shorten((query || "").trim(), 160);
    if (!normalized || !q) return [];
    const store = await ensureStateLoaded();
    const rows = (store.memos[normalized] || []).slice();
    const qTokens = tokenize(q);
    const scored = rows
        .map((x) => ({
            item: x,
            score: overlapScore(qTokens, tokenize(x.text)) + overlapScore(qTokens, x.tags),
        }))
        .filter((x) => x.score > 0)
        .sort((a, b) => (b.score !== a.score ? b.score - a.score : b.item.ts - a.item.ts))
        .slice(0, clamp(limit, 1, 20))
        .map((x) => x.item);
    return scored;
}

export async function deleteMemoEntry(userKey: string, selector: string): Promise<{ ok: boolean; removed?: MemoEntry }> {
    const normalized = normalizeUserKey(userKey);
    const sel = (selector || "").trim();
    if (!normalized || !sel) return { ok: false };
    const store = await ensureStateLoaded();
    const arr = (store.memos[normalized] || []).slice();
    if (arr.length === 0) return { ok: false };

    let idx = -1;
    if (/^\d+$/.test(sel)) {
        const n = Number(sel);
        if (n >= 1 && n <= arr.length) {
            idx = arr.length - n;
        }
    }
    if (idx < 0) {
        idx = arr.findIndex((x) => x.id === sel);
    }
    if (idx < 0 || idx >= arr.length) {
        return { ok: false };
    }
    const [removed] = arr.splice(idx, 1);
    store.memos[normalized] = arr;
    await persistState();
    return { ok: true, removed };
}

export async function hasGithubWeeklyPushed(userKey: string, weekKey: string): Promise<boolean> {
    const normalized = normalizeUserKey(userKey);
    if (!normalized || !weekKey) return false;
    const store = await ensureStateLoaded();
    return (store.githubWeekly[normalized]?.weekKey || "") === weekKey;
}

export async function markGithubWeeklyPushed(userKey: string, weekKey: string): Promise<void> {
    const normalized = normalizeUserKey(userKey);
    if (!normalized || !weekKey) return;
    const store = await ensureStateLoaded();
    store.githubWeekly[normalized] = {
        weekKey,
        ts: Date.now(),
    };
    await persistState();
}

export async function retrieveRagHits(userKey: string, query: string, limit: number): Promise<RagHit[]> {
    const store = await ensureStateLoaded();
    const normalized = normalizeUserKey(userKey);
    const q = shorten(query, 400);
    const qTokens = tokenize(q);

    const hits: RagHit[] = [];

    for (const n of store.notes[normalized] || []) {
        const tokens = tokenize(n.text);
        const score = overlapScore(qTokens, tokens);
        if (score > 0) {
            hits.push({ score, ts: n.ts, text: n.text, from: "note" });
        }
    }

    for (const c of store.chats[normalized] || []) {
        const tokens = tokenize(c.text);
        const score = overlapScore(qTokens, tokens);
        if (score > 0) {
            hits.push({ score, ts: c.ts, text: `${c.role}: ${c.text}`, from: "chat" });
        }
    }

    hits.sort((a, b) => {
        if (b.score !== a.score) {
            return b.score - a.score;
        }
        return b.ts - a.ts;
    });

    return hits.slice(0, clamp(limit, 1, 8));
}

const REFLECTION_STOPWORDS = new Set([
    "今天", "明天", "后天", "这个", "那个", "然后", "就是", "感觉", "一下", "现在",
    "我们", "你们", "他们", "因为", "所以", "但是", "如果", "还是", "已经", "可以",
    "不要", "你好", "哈哈", "嗯嗯", "好的", "知道", "谢谢",
]);

function isLowSignalText(text: string): boolean {
    const t = (text || "").trim();
    if (!t) return true;
    if (t.length <= 2) return true;
    const low = ["早", "晚安", "哈哈", "嗯", "哦", "ok", "收到", "在吗", "好吧"];
    return low.some((k) => t.toLowerCase() === k || t.includes(k));
}

export function summarizeForReflection(chats: ChatEntry[], hours: number): string | null {
    if (!Array.isArray(chats) || chats.length === 0) {
        return null;
    }
    const cutoff = Date.now() - clamp(hours, 1, 168) * 3600 * 1000;
    const recent = chats.filter((x) => Number(x.ts || 0) >= cutoff);
    const userTexts = recent
        .filter((x) => x.role === "user")
        .map((x) => (x.text || "").trim())
        .filter((t) => !!t && !isLowSignalText(t));
    if (userTexts.length < 3) {
        return null;
    }

    const tokenFreq = new Map<string, number>();
    for (const text of userTexts) {
        for (const tok of tokenize(text)) {
            if (tok.length < 2 || tok.length > 20) continue;
            if (REFLECTION_STOPWORDS.has(tok)) continue;
            if (/^\d+$/.test(tok)) continue;
            tokenFreq.set(tok, (tokenFreq.get(tok) || 0) + 1);
        }
    }

    const topics = [...tokenFreq.entries()]
        .sort((a, b) => b[1] - a[1])
        .slice(0, 6)
        .map(([k]) => k);

    const recentSignals = userTexts
        .slice(-3)
        .map((t) => shorten(t, 36))
        .filter(Boolean);

    const parts: string[] = [];
    parts.push(`用户近${hours}小时有${userTexts.length}条有效表达。`);
    if (topics.length > 0) {
        parts.push(`高频关注：${topics.join("、")}。`);
    }
    if (recentSignals.length > 0) {
        parts.push(`近期线索：${recentSignals.join("；")}。`);
    }
    return parts.join("");
}

export async function runDailyReflection(params: {
    userKey: string;
    hours: number;
    minUserMessages: number;
}): Promise<{ ok: boolean; saved: boolean; reason?: string; userKey: string; summary?: string }> {
    const userKey = normalizeUserKey(params.userKey);
    const hours = clamp(Number(params.hours || 24), 1, 168);
    const minUserMessages = clamp(Number(params.minUserMessages || 5), 3, 60);
    if (!userKey || userKey === "session:unknown") {
        return { ok: false, saved: false, reason: "invalid_user_key", userKey };
    }

    const recent = await getRecentChats(userKey, MAX_CHATS_PER_USER);
    const cutoff = Date.now() - hours * 3600 * 1000;
    const userCount = recent.filter((x) => x.role === "user" && Number(x.ts || 0) >= cutoff).length;
    if (userCount < minUserMessages) {
        return { ok: true, saved: false, reason: "insufficient_messages", userKey };
    }

    const summary = summarizeForReflection(recent, hours);
    if (!summary) {
        return { ok: true, saved: false, reason: "no_signal", userKey };
    }
    await addMemoryNote(userKey, summary, "derived");
    return { ok: true, saved: true, userKey, summary };
}
