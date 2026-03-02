import { randomUUID } from "node:crypto";
import { promises as fs } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";

export type ObsMetric = {
    ts: string;
    request_id: string;
    user_key: string;
    tool_name: string;
    latency_ms: number;
    error_code: string;
};

export type ToolResult = {
    content: Array<{ type: "text"; text: string }>;
    details: unknown;
};

export function jsonResult(payload: unknown): ToolResult {
    return {
        content: [{ type: "text", text: JSON.stringify(payload, null, 2) }],
        details: payload,
    };
}

export function resolveObsFilePath(): string {
    const fromEnv = (process.env.XIAO_OBS_FILE || "").trim();
    if (fromEnv) {
        return fromEnv;
    }
    return path.join(homedir(), ".openclaw", "xiao-core", "observability.jsonl");
}

export function resolveObsUserKey(params: unknown): string {
    const p = (params || {}) as Record<string, unknown>;
    const raw =
        (typeof p.userKey === "string" && p.userKey.trim()) ||
        (typeof p.to === "string" && p.to.trim()) ||
        "unknown";
    const qq = raw.match(/^qqbot:(?:c2c|group):([A-Za-z0-9._:-]{6,128})$/i);
    if (qq?.[1]) {
        return `qqbot:${qq[1]}`;
    }
    return raw.slice(0, 160);
}

export async function writeObsMetric(metric: ObsMetric): Promise<void> {
    try {
        const file = resolveObsFilePath();
        await fs.mkdir(path.dirname(file), { recursive: true });
        await fs.appendFile(file, `${JSON.stringify(metric)}\n`, "utf8");
    } catch {
        // best effort metrics logging
    }
}

export async function obsWrap(toolName: string, userKey: string, startedAt: number, payload: unknown): Promise<ToolResult> {
    const obj = (payload || {}) as Record<string, unknown>;
    const errorCode =
        obj && obj.ok === false ? String(obj.error || "tool_error").slice(0, 120) : "";
    await writeObsMetric({
        ts: new Date().toISOString(),
        request_id: randomUUID(),
        user_key: userKey || "unknown",
        tool_name: toolName,
        latency_ms: Math.max(0, Date.now() - startedAt),
        error_code: errorCode,
    });
    return jsonResult(payload);
}
