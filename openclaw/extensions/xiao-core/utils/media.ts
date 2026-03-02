import { shorten } from "../../shared/text.js";
import { normalizeUserKey } from "../../shared/identity.js";

const PENDING_URL_TTL_MS = 3 * 3600 * 1000;
const PENDING_IMAGE_TTL_MS = 2 * 3600 * 1000;

export interface PendingUrl {
    url: string;
    seenAt: number;
    sourceInput: string;
}

export interface PendingImage {
    refs: string[];
    seenAt: number;
    sourceInput: string;
}

const PENDING_URL_BY_USER = new Map<string, PendingUrl>();
const PENDING_IMAGE_BY_USER = new Map<string, PendingImage>();

export function extractUrls(input: string): string[] {
    const text = (input || "").trim();
    if (!text) return [];
    const matches = text.match(/https?:\/\/[^\s<>"'`，。！？、]+/gi) || [];
    const out: string[] = [];
    const seen = new Set<string>();
    for (const m of matches) {
        const url = m.trim();
        if (!url || seen.has(url)) continue;
        seen.add(url);
        out.push(url);
        if (out.length >= 5) break;
    }
    return out;
}

export function normalizeImageRef(raw: string): string {
    let ref = (raw || "").trim();
    if (!ref) {
        return "";
    }

    ref = ref.replace(/^[<\s]+|[>\s]+$/g, "");
    ref = ref.replace(/[，。；;,]+$/g, "");

    const fileMatch = ref.match(/^file:\/\/\/?(.*)$/i);
    if (fileMatch?.[1]) {
        const body = fileMatch[1].trim();
        if (/^[A-Za-z]:[\\/]/.test(body)) {
            ref = body;
        } else {
            ref = `/${body.replace(/^\/+/, "")}`;
        }
    }

    return ref;
}

export function extractImageRefs(input: string): string[] {
    const text = (input || "").trim();
    if (!text) return [];

    const refs: string[] = [];
    const seen = new Set<string>();
    const patterns = [
        /(?:^|\n)\s*-\s*图片地址\s*[：:]\s*([^\n\r]+)/gim,
        /(?:^|\n)\s*(?:MediaPath|MediaUrl)\s*[：:]\s*([^\n\r]+)/gim,
        /<qqimg>\s*([^<>\n]+?)\s*<\/(?:qqimg|img)>/gim,
        /<img\b[^>]*\bsrc=["']([^"']+)["'][^>]*>/gim,
    ];

    for (const pattern of patterns) {
        let match: RegExpExecArray | null;
        while ((match = pattern.exec(text)) !== null) {
            const ref = normalizeImageRef(match[1] || "");
            if (!ref || seen.has(ref)) {
                continue;
            }
            seen.add(ref);
            refs.push(ref);
            if (refs.length >= 6) {
                return refs;
            }
        }
    }

    return refs;
}

export function normalizeAudioRef(raw: string): string {
    let ref = (raw || "").trim();
    if (!ref) {
        return "";
    }
    ref = ref.replace(/^[<\s]+|[>\s]+$/g, "");
    ref = ref.replace(/[，。；;,]+$/g, "");
    const fileMatch = ref.match(/^file:\/\/\/?(.*)$/i);
    if (fileMatch?.[1]) {
        const body = fileMatch[1].trim();
        if (/^[A-Za-z]:[\\/]/.test(body)) {
            ref = body;
        } else {
            ref = `/${body.replace(/^\/+/, "")}`;
        }
    }
    return ref;
}

export function extractAudioRefs(input: string): string[] {
    const text = (input || "").trim();
    if (!text) return [];

    const refs: string[] = [];
    const seen = new Set<string>();
    const patterns = [
        /(?:^|\n)\s*-\s*语音文件\s*[：:]\s*([^\n\r]+)/gim,
        /(?:^|\n)\s*(?:AudioPath|audioPath)\s*[：:]\s*([^\n\r]+)/gim,
    ];

    for (const pattern of patterns) {
        let match: RegExpExecArray | null;
        while ((match = pattern.exec(text)) !== null) {
            const ref = normalizeAudioRef(match[1] || "");
            if (!ref || seen.has(ref)) {
                continue;
            }
            seen.add(ref);
            refs.push(ref);
            if (refs.length >= 4) {
                return refs;
            }
        }
    }
    return refs;
}

export function normalizeEvidenceUrl(url: string): string {
    const raw = (url || "").trim();
    if (!raw) return "";
    try {
        const u = new URL(raw);
        if (!/^https?:$/i.test(u.protocol)) return "";
        return u.toString();
    } catch {
        return "";
    }
}

export function sweepPendingUrlCache(now: number): void {
    for (const [k, v] of PENDING_URL_BY_USER.entries()) {
        if (now - v.seenAt > PENDING_URL_TTL_MS) {
            PENDING_URL_BY_USER.delete(k);
        }
    }
}

export function setPendingUrl(userKey: string, url: string, sourceInput: string): void {
    const key = normalizeUserKey(userKey);
    if (!key) return;
    PENDING_URL_BY_USER.set(key, {
        url: shorten(url, 600),
        seenAt: Date.now(),
        sourceInput: shorten(sourceInput, 240),
    });
}

export function getPendingUrl(userKey: string): PendingUrl | null {
    const key = normalizeUserKey(userKey);
    const p = PENDING_URL_BY_USER.get(key);
    if (!p) return null;
    if (Date.now() - p.seenAt > PENDING_URL_TTL_MS) {
        PENDING_URL_BY_USER.delete(key);
        return null;
    }
    return p;
}

export function sweepPendingImageCache(now: number): void {
    for (const [k, v] of PENDING_IMAGE_BY_USER.entries()) {
        if (now - v.seenAt > PENDING_IMAGE_TTL_MS) {
            PENDING_IMAGE_BY_USER.delete(k);
        }
    }
}

export function setPendingImage(userKey: string, refs: string[], sourceInput: string): void {
    const key = normalizeUserKey(userKey);
    if (!key) return;
    const normalized = refs
        .map((r) => normalizeImageRef(r))
        .filter(Boolean)
        .slice(0, 6);
    if (normalized.length === 0) {
        return;
    }
    PENDING_IMAGE_BY_USER.set(key, {
        refs: normalized,
        seenAt: Date.now(),
        sourceInput: shorten(sourceInput || "", 240),
    });
}

export function getPendingImage(userKey: string): PendingImage | null {
    const key = normalizeUserKey(userKey);
    const p = PENDING_IMAGE_BY_USER.get(key);
    if (!p) return null;
    if (Date.now() - p.seenAt > PENDING_IMAGE_TTL_MS) {
        PENDING_IMAGE_BY_USER.delete(key);
        return null;
    }
    return p;
}

export function clearPendingImage(userKey: string): void {
    const key = normalizeUserKey(userKey);
    if (!key) return;
    PENDING_IMAGE_BY_USER.delete(key);
}
