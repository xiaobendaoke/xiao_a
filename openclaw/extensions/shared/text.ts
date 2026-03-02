export function clamp(n: number, lo: number, hi: number): number {
    return Math.max(lo, Math.min(hi, n));
}

export function shorten(text: string, maxLen: number): string {
    const t = (text || "").replace(/\s+/g, " ").trim();
    if (t.length <= maxLen) {
        return t;
    }
    return `${t.slice(0, maxLen)}...`;
}

export function errToString(err: unknown): string {
    if (err instanceof Error) {
        return err.message;
    }
    if (typeof err === "string") {
        return err;
    }
    if (err && typeof err === "object") {
        try {
            return JSON.stringify(err);
        } catch {
            return String(err);
        }
    }
    return String(err);
}

export function cleanAssistantText(text: string): string {
    let cleaned = (text || "").trim();
    cleaned = cleaned.replace(/\[MOOD_CHANGE[:：]\s*-?\d+\s*\]/gi, "");
    cleaned = cleaned.replace(/\[UPDATE_PROFILE[:：]\s*[^\]]+\]/gi, "");
    cleaned = cleaned.replace(/<qqvoice>[\s\S]*?<\/qqvoice>/gi, "");
    cleaned = cleaned.replace(/<qqimg>[\s\S]*?<\/qqimg>/gi, "");
    cleaned = cleaned.replace(/<img\b[^>]*>/gi, "");
    cleaned = cleaned.replace(/!\[[^\]]*]\((?:file|https?):\/\/[^)]+\)/gi, "");
    cleaned = cleaned.replace(/\[\[\s*audio_as_voice\s*]\]/gi, "");
    cleaned = cleaned.replace(/\s+$/g, "");
    // Clean potentially lingering thought tags
    cleaned = cleaned.replace(/<[Mm]emo>[\s\S]*?<\/[Mm]emo>/g, "");
    cleaned = cleaned.replace(/<[Rr]ethink>[\s\S]*?<\/[Rr]ethink>/g, "");
    cleaned = cleaned.replace(/<think>[\s\S]*?<\/think>/g, "");
    return cleaned.trim();
}
