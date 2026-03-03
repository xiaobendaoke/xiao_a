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
    plans: Record<string, PlanEntry[]>;
    habits: Record<string, HabitEntry[]>;
    diary: Record<string, DiaryEntry[]>;
    games: Record<string, GameSession | null>;
    greetings: Record<string, GreetingLog>;
    persona: Record<string, string>;
};

export type PlanEntry = {
    id: string;
    content: string;
    when: string;
    place: string;
    status: "pending" | "done" | "cancelled" | "expired";
    remindCount: number;
    lastRemindTs: number;
    ts: number;
    updatedTs: number;
};

export type HabitEntry = {
    id: string;
    name: string;
    type: string;
    targetTime: string;
    targetValue: number;
    currentStreak: number;
    maxStreak: number;
    totalCheckins: number;
    lastCheckinDate: string;
    active: boolean;
    ts: number;
    updatedTs: number;
};

export type HabitCheckinResult = {
    ok: boolean;
    message: string;
    habit?: HabitEntry;
};

export type DiaryEntry = {
    date: string;
    mood: number;
    label: string;
    note: string;
    events: string[];
    ts: number;
};

export type GameSession = {
    gameType: "truth_dare" | "love_words" | "riddle" | "qa";
    status: "playing" | "finished";
    round: number;
    score: number;
    data: Record<string, string>;
    updatedTs: number;
};

export type GreetingLog = {
    morningDates: string[];
    nightDates: string[];
    noonDates: string[];
    lastType: string;
    lastTs: number;
};

export const STARTED_AT = Date.now();
export const SESSION_USER_MAP = new Map<string, SessionSnapshot>();

export const DEFAULT_CORE_STATE: CoreState = {
    notes: {},
    chats: {},
    links: {},
    memos: {},
    githubWeekly: {},
    plans: {},
    habits: {},
    diary: {},
    games: {},
    greetings: {},
    persona: {},
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
            plans: parsed.plans && typeof parsed.plans === "object" ? parsed.plans : {},
            habits: parsed.habits && typeof parsed.habits === "object" ? parsed.habits : {},
            diary: parsed.diary && typeof parsed.diary === "object" ? parsed.diary : {},
            games: parsed.games && typeof parsed.games === "object" ? parsed.games : {},
            greetings: parsed.greetings && typeof parsed.greetings === "object" ? parsed.greetings : {},
            persona: parsed.persona && typeof parsed.persona === "object" ? parsed.persona : {},
        };
        return stateCache;
    } catch {
        stateCache = {
            notes: {},
            chats: {},
            links: {},
            memos: {},
            githubWeekly: {},
            plans: {},
            habits: {},
            diary: {},
            games: {},
            greetings: {},
            persona: {},
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

function makeEntityId(prefix: string): string {
    return `${prefix}_${Date.now().toString(36)}_${Math.trunc(Math.random() * 1e6).toString(36)}`;
}

function dateKey(ts: number = Date.now()): string {
    return new Date(ts).toISOString().slice(0, 10);
}

function moodLabel(v: number): string {
    if (v >= 80) return "超开心";
    if (v >= 30) return "开心";
    if (v >= -10) return "一般";
    if (v >= -50) return "有点低落";
    return "难过";
}

export async function setUserPersona(userKey: string, personaKey: string): Promise<boolean> {
    const normalized = normalizeUserKey(userKey);
    if (!normalized || !personaKey.trim()) return false;
    const store = await ensureStateLoaded();
    store.persona[normalized] = personaKey.trim();
    await persistState();
    return true;
}

export async function getUserPersona(userKey: string): Promise<string> {
    const store = await ensureStateLoaded();
    const normalized = normalizeUserKey(userKey);
    return store.persona[normalized] || "default";
}

export async function addPlanEntry(
    userKey: string,
    content: string,
    when: string = "",
    place: string = "",
): Promise<PlanEntry | null> {
    const normalized = normalizeUserKey(userKey);
    const clean = shorten((content || "").trim(), 180);
    if (!normalized || !clean) return null;
    const store = await ensureStateLoaded();
    const arr = store.plans[normalized] || [];
    const now = Date.now();
    const entry: PlanEntry = {
        id: makeEntityId("plan"),
        content: clean,
        when: shorten((when || "").trim(), 40),
        place: shorten((place || "").trim(), 40),
        status: "pending",
        remindCount: 0,
        lastRemindTs: 0,
        ts: now,
        updatedTs: now,
    };
    arr.push(entry);
    store.plans[normalized] = arr.slice(-200);
    await persistState();
    return entry;
}

export async function listPlanEntries(userKey: string, status?: PlanEntry["status"]): Promise<PlanEntry[]> {
    const store = await ensureStateLoaded();
    const arr = (store.plans[normalizeUserKey(userKey)] || []).slice();
    const rows = status ? arr.filter((x) => x.status === status) : arr;
    return rows.sort((a, b) => b.updatedTs - a.updatedTs);
}

export async function updatePlanStatus(
    userKey: string,
    selector: string,
    status: PlanEntry["status"],
): Promise<PlanEntry | null> {
    const normalized = normalizeUserKey(userKey);
    const sel = (selector || "").trim();
    if (!normalized || !sel) return null;
    const store = await ensureStateLoaded();
    const arr = store.plans[normalized] || [];
    let idx = arr.findIndex((x) => x.id === sel);
    if (idx < 0 && /^\d+$/.test(sel)) {
        const n = Number(sel);
        const sorted = arr.slice().sort((a, b) => b.updatedTs - a.updatedTs);
        const target = sorted[n - 1];
        if (target) {
            idx = arr.findIndex((x) => x.id === target.id);
        }
    }
    if (idx < 0) return null;
    arr[idx] = { ...arr[idx], status, updatedTs: Date.now() };
    store.plans[normalized] = arr;
    await persistState();
    return arr[idx];
}

export async function increasePlanRemindCount(userKey: string, planId: string): Promise<void> {
    const normalized = normalizeUserKey(userKey);
    const store = await ensureStateLoaded();
    const arr = store.plans[normalized] || [];
    const idx = arr.findIndex((x) => x.id === planId);
    if (idx < 0) return;
    const now = Date.now();
    const next = (arr[idx].remindCount || 0) + 1;
    arr[idx] = {
        ...arr[idx],
        remindCount: next,
        lastRemindTs: now,
        status: next >= 5 ? "expired" : arr[idx].status,
        updatedTs: now,
    };
    store.plans[normalized] = arr;
    await persistState();
}

export async function upsertHabit(userKey: string, name: string, targetTime: string = ""): Promise<HabitEntry | null> {
    const normalized = normalizeUserKey(userKey);
    const clean = shorten((name || "").trim(), 40);
    if (!normalized || !clean) return null;
    const store = await ensureStateLoaded();
    const arr = store.habits[normalized] || [];
    const now = Date.now();
    const idx = arr.findIndex((x) => x.name === clean);
    if (idx >= 0) {
        arr[idx] = {
            ...arr[idx],
            active: true,
            targetTime: shorten((targetTime || "").trim(), 8) || arr[idx].targetTime,
            updatedTs: now,
        };
        store.habits[normalized] = arr;
        await persistState();
        return arr[idx];
    }
    const entry: HabitEntry = {
        id: makeEntityId("habit"),
        name: clean,
        type: "custom",
        targetTime: shorten((targetTime || "").trim(), 8),
        targetValue: 1,
        currentStreak: 0,
        maxStreak: 0,
        totalCheckins: 0,
        lastCheckinDate: "",
        active: true,
        ts: now,
        updatedTs: now,
    };
    arr.push(entry);
    store.habits[normalized] = arr.slice(-100);
    await persistState();
    return entry;
}

export async function listHabits(userKey: string, onlyActive: boolean = true): Promise<HabitEntry[]> {
    const store = await ensureStateLoaded();
    const arr = (store.habits[normalizeUserKey(userKey)] || []).slice();
    const rows = onlyActive ? arr.filter((x) => x.active) : arr;
    return rows.sort((a, b) => b.updatedTs - a.updatedTs);
}

export async function cancelHabit(userKey: string, selector: string): Promise<HabitEntry | null> {
    const normalized = normalizeUserKey(userKey);
    const sel = (selector || "").trim();
    if (!normalized || !sel) return null;
    const store = await ensureStateLoaded();
    const arr = store.habits[normalized] || [];
    const idx = arr.findIndex((x) => x.id === sel || x.name === sel);
    if (idx < 0) return null;
    arr[idx] = { ...arr[idx], active: false, updatedTs: Date.now() };
    store.habits[normalized] = arr;
    await persistState();
    return arr[idx];
}

export async function checkinHabit(userKey: string, selector: string): Promise<HabitCheckinResult> {
    const normalized = normalizeUserKey(userKey);
    const sel = (selector || "").trim();
    if (!normalized || !sel) return { ok: false, message: "请提供习惯名" };
    const store = await ensureStateLoaded();
    const arr = store.habits[normalized] || [];
    const idx = arr.findIndex((x) => (x.id === sel || x.name === sel) && x.active);
    if (idx < 0) return { ok: false, message: "没有找到这个习惯" };
    const today = dateKey();
    const item = arr[idx];
    if (item.lastCheckinDate === today) {
        return { ok: false, message: `今天的${item.name}已经打过卡了` };
    }
    const yesterday = dateKey(Date.now() - 86400000);
    const streak = item.lastCheckinDate === yesterday ? item.currentStreak + 1 : 1;
    const next: HabitEntry = {
        ...item,
        currentStreak: streak,
        maxStreak: Math.max(streak, item.maxStreak),
        totalCheckins: item.totalCheckins + 1,
        lastCheckinDate: today,
        updatedTs: Date.now(),
    };
    arr[idx] = next;
    store.habits[normalized] = arr;
    await persistState();
    return { ok: true, message: `${next.name}打卡成功，连续${next.currentStreak}天`, habit: next };
}

export async function addDiaryEntry(
    userKey: string,
    mood: number,
    note: string = "",
    events: string[] = [],
): Promise<DiaryEntry | null> {
    const normalized = normalizeUserKey(userKey);
    if (!normalized) return null;
    const store = await ensureStateLoaded();
    const arr = store.diary[normalized] || [];
    const day = dateKey();
    const next: DiaryEntry = {
        date: day,
        mood: clamp(Number(mood || 0), -100, 100),
        label: moodLabel(clamp(Number(mood || 0), -100, 100)),
        note: shorten((note || "").trim(), 180),
        events: (events || []).map((x) => shorten(x, 40)).slice(0, 8),
        ts: Date.now(),
    };
    const idx = arr.findIndex((x) => x.date === day);
    if (idx >= 0) {
        arr[idx] = next;
    } else {
        arr.push(next);
    }
    arr.sort((a, b) => a.date.localeCompare(b.date));
    store.diary[normalized] = arr.slice(-90);
    await persistState();
    return next;
}

export async function getDiaryEntries(userKey: string, days: number): Promise<DiaryEntry[]> {
    const store = await ensureStateLoaded();
    const arr = (store.diary[normalizeUserKey(userKey)] || []).slice();
    return arr.slice(-clamp(days, 1, 90));
}

export async function setGameSession(userKey: string, session: GameSession | null): Promise<void> {
    const normalized = normalizeUserKey(userKey);
    if (!normalized) return;
    const store = await ensureStateLoaded();
    store.games[normalized] = session;
    await persistState();
}

export async function getGameSession(userKey: string): Promise<GameSession | null> {
    const store = await ensureStateLoaded();
    return store.games[normalizeUserKey(userKey)] || null;
}

export async function recordGreeting(userKey: string, greetType: "morning" | "night" | "noon"): Promise<void> {
    const normalized = normalizeUserKey(userKey);
    if (!normalized) return;
    const store = await ensureStateLoaded();
    const log: GreetingLog = store.greetings[normalized] || {
        morningDates: [],
        nightDates: [],
        noonDates: [],
        lastType: "",
        lastTs: 0,
    };
    const day = dateKey();
    const key = greetType === "morning" ? "morningDates" : greetType === "night" ? "nightDates" : "noonDates";
    const set = new Set(log[key]);
    set.add(day);
    log[key] = [...set].slice(-30);
    log.lastType = greetType;
    log.lastTs = Date.now();
    store.greetings[normalized] = log;
    await persistState();
}

export async function getGreetingLog(userKey: string): Promise<GreetingLog | null> {
    const store = await ensureStateLoaded();
    return store.greetings[normalizeUserKey(userKey)] || null;
}

export async function getLoveScore(userKey: string): Promise<{
    score: number;
    level: string;
    details: Record<string, number>;
}> {
    const normalized = normalizeUserKey(userKey);
    const store = await ensureStateLoaded();
    const now = Date.now();
    const chats = (store.chats[normalized] || []).filter((x) => now - x.ts <= 7 * 86400000);
    const notes = store.notes[normalized] || [];
    const moodApprox = (store.greetings[normalized]?.nightDates.length || 0) * 2;
    const userMsgCount = chats.filter((x) => x.role === "user").length;
    const freqScore = clamp(Math.round((userMsgCount / 100) * 30), 0, 30);
    const moodScore = clamp(Math.round(((clamp(moodApprox, -100, 100) + 100) / 200) * 30), 0, 30);
    const memoryScore = clamp(Math.round((notes.length / 100) * 20), 0, 20);
    const proactiveScore = clamp(Math.round(((store.githubWeekly[normalized] ? 1 : 0.5) * 20)), 0, 20);
    const total = clamp(freqScore + moodScore + memoryScore + proactiveScore, 0, 100);
    const level = total >= 90 ? "灵魂伴侣" : total >= 75 ? "热恋期" : total >= 60 ? "甜蜜期" : total >= 40 ? "平稳期" : "考察期";
    return {
        score: total,
        level,
        details: {
            互动频率: freqScore,
            情绪状态: moodScore,
            记忆浓度: memoryScore,
            主动互动: proactiveScore,
        },
    };
}
