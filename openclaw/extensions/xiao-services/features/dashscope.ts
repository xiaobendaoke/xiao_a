import { fetchJson, fetchBytes } from "../../shared/request.js";

function resolveDashscopeAigcEndpoint(baseUrl: string): string {
    const fallback = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation";
    try {
        const u = new URL(baseUrl);
        return `${u.origin}/api/v1/services/aigc/multimodal-generation/generation`;
    } catch {
        return fallback;
    }
}

export async function callVisionDashscopeAigc(params: {
    apiKey: string;
    baseUrl: string;
    model: string;
    imageUrl: string;
    prompt?: string;
    timeoutMs?: number;
}): Promise<{ text: string; raw: unknown }> {
    const endpoint = resolveDashscopeAigcEndpoint(params.baseUrl);
    const content: Array<Record<string, unknown>> = [{ image: params.imageUrl }];
    if (params.prompt) {
        content.push({ text: params.prompt });
    }

    const payload = {
        model: params.model,
        input: {
            messages: [
                {
                    role: "user",
                    content,
                },
            ],
        },
        parameters: {},
    };

    const raw = await fetchJson(
        endpoint,
        {
            method: "POST",
            headers: {
                Authorization: `Bearer ${params.apiKey}`,
                "Content-Type": "application/json",
            },
            body: JSON.stringify(payload),
        },
        params.timeoutMs || 45000,
    );

    const text =
        (raw as Record<string, unknown>)?.output &&
            typeof (raw as Record<string, unknown>).output === "object"
            ? (() => {
                const output = (raw as Record<string, unknown>).output as Record<string, unknown>;
                const choices = output.choices;
                if (!Array.isArray(choices) || !choices[0] || typeof choices[0] !== "object") {
                    return "";
                }
                const message = (choices[0] as Record<string, unknown>).message as Record<string, unknown> | undefined;
                if (!message) {
                    return "";
                }
                const contentList = message.content;
                if (!Array.isArray(contentList)) {
                    return "";
                }
                for (const item of contentList) {
                    if (!item || typeof item !== "object") continue;
                    const t = (item as Record<string, unknown>).text;
                    if (typeof t === "string" && t.trim()) {
                        return t.trim();
                    }
                }
                return "";
            })()
            : "";

    if (!text) {
        throw new Error(`Vision response missing text: ${JSON.stringify(raw).slice(0, 280)}`);
    }

    return { text, raw };
}

export async function callAsrDashscopeAigc(params: {
    apiKey: string;
    baseUrl: string;
    model: string;
    audio: { bytes: Uint8Array; mimeType: string; filename: string };
    prompt?: string;
    timeoutMs?: number;
}): Promise<{ text: string; raw: unknown }> {
    const endpoint = resolveDashscopeAigcEndpoint(params.baseUrl);
    const mime = params.audio.mimeType || "audio/wav";
    const audioDataUrl = `data:${mime};base64,${Buffer.from(params.audio.bytes).toString("base64")}`;

    const content: Array<Record<string, unknown>> = [{ audio: audioDataUrl }];
    if (params.prompt) {
        content.push({ text: params.prompt });
    }

    const payload: Record<string, unknown> = {
        model: params.model,
        input: {
            messages: [
                {
                    role: "user",
                    content,
                },
            ],
        },
        parameters: {
            asr_options: {
                sample_rate: 16000,
                channel: 1,
            },
        },
    };

    const raw = await fetchJson(
        endpoint,
        {
            method: "POST",
            headers: {
                Authorization: `Bearer ${params.apiKey}`,
                "Content-Type": "application/json",
            },
            body: JSON.stringify(payload),
        },
        params.timeoutMs || 60000,
    );

    const text =
        (raw as Record<string, unknown>)?.output &&
            typeof (raw as Record<string, unknown>).output === "object"
            ? (() => {
                const output = (raw as Record<string, unknown>).output as Record<string, unknown>;
                const choices = output.choices;
                if (!Array.isArray(choices) || !choices[0] || typeof choices[0] !== "object") {
                    return "";
                }
                const message = (choices[0] as Record<string, unknown>).message as Record<string, unknown> | undefined;
                if (!message) {
                    return "";
                }
                const contentList = message.content;
                if (!Array.isArray(contentList)) {
                    return "";
                }
                for (const item of contentList) {
                    if (!item || typeof item !== "object") continue;
                    const t = (item as Record<string, unknown>).text;
                    if (typeof t === "string" && t.trim()) {
                        return t.trim();
                    }
                }
                return "";
            })()
            : "";

    if (!text) {
        throw new Error(`ASR response missing text: ${JSON.stringify(raw).slice(0, 280)}`);
    }
    return { text, raw };
}

export async function callTtsDashscopeAigc(params: {
    apiKey: string;
    baseUrl: string;
    model: string;
    voice: string;
    input: string;
    format: string;
    rate?: number;
    pitch?: number;
    volume?: number;
    timeoutMs?: number;
}): Promise<{ audioBytes: Uint8Array; mimeType: string; raw: unknown }> {
    const endpoint = resolveDashscopeAigcEndpoint(params.baseUrl);
    const payload = {
        model: params.model,
        input: { text: params.input },
        parameters: {
            voice: params.voice,
            format: params.format,
            rate: params.rate,
            pitch: params.pitch,
            volume: params.volume,
        },
    };

    const raw = (await fetchJson(
        endpoint,
        {
            method: "POST",
            headers: {
                Authorization: `Bearer ${params.apiKey}`,
                "Content-Type": "application/json",
            },
            body: JSON.stringify(payload),
        },
        params.timeoutMs || 60000,
    )) as Record<string, unknown>;

    const output = (raw.output || {}) as Record<string, unknown>;
    const audio = (output.audio || {}) as Record<string, unknown>;
    const b64 = typeof audio.data === "string" ? audio.data.trim() : "";
    if (b64) {
        return {
            audioBytes: new Uint8Array(Buffer.from(b64, "base64")),
            mimeType: params.format === "wav" ? "audio/wav" : params.format === "ogg" ? "audio/ogg" : "audio/mpeg",
            raw,
        };
    }

    const url = typeof audio.url === "string" ? audio.url.trim() : "";
    if (url) {
        const downloaded = await fetchBytes(url, undefined, 30000);
        return {
            audioBytes: downloaded.bytes,
            mimeType: downloaded.contentType || "application/octet-stream",
            raw,
        };
    }

    throw new Error(`TTS response missing audio data/url: ${JSON.stringify(raw).slice(0, 280)}`);
}
