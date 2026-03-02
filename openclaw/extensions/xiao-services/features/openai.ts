export async function callAsrOpenAICompat(params: {
    apiKey: string;
    baseUrl: string;
    model: string;
    audio: { bytes: Uint8Array; mimeType: string; filename: string };
    language?: string;
    prompt?: string;
    timeoutMs?: number;
}): Promise<{ text: string; raw: unknown }> {
    const url = `${params.baseUrl.replace(/\/$/, "")}/audio/transcriptions`;
    const form = new FormData();
    form.append("model", params.model);
    if (params.language) {
        form.append("language", params.language);
    }
    if (params.prompt) {
        form.append("prompt", params.prompt);
    }
    form.append(
        "file",
        new Blob([params.audio.bytes], { type: params.audio.mimeType || "application/octet-stream" }),
        params.audio.filename,
    );

    const res = await fetch(url, {
        method: "POST",
        headers: {
            Authorization: `Bearer ${params.apiKey}`,
        },
        body: form,
        signal: AbortSignal.timeout(params.timeoutMs || 45000),
    });

    const textBody = await res.text();
    if (!res.ok) {
        throw new Error(`ASR HTTP ${res.status}: ${textBody.slice(0, 300)}`);
    }

    let raw: unknown = textBody;
    try {
        raw = JSON.parse(textBody);
    } catch {
        raw = textBody;
    }

    if (raw && typeof raw === "object") {
        const obj = raw as Record<string, unknown>;
        const text =
            (typeof obj.text === "string" && obj.text) ||
            (typeof obj.output_text === "string" && obj.output_text) ||
            (typeof obj.transcript === "string" && obj.transcript) ||
            "";
        return { text: text.trim(), raw };
    }

    return {
        text: String(raw || "").trim(),
        raw,
    };
}

export async function callTtsOpenAICompat(params: {
    apiKey: string;
    baseUrl: string;
    model: string;
    voice: string;
    input: string;
    format: string;
    instructions?: string;
    timeoutMs?: number;
}): Promise<{ audioBytes: Uint8Array; mimeType: string }> {
    const url = `${params.baseUrl.replace(/\/$/, "")}/audio/speech`;
    const payload: Record<string, unknown> = {
        model: params.model,
        input: params.input,
        voice: params.voice,
        format: params.format,
        response_format: params.format,
    };
    if (params.instructions) {
        payload.instructions = params.instructions;
    }

    const res = await fetch(url, {
        method: "POST",
        headers: {
            Authorization: `Bearer ${params.apiKey}`,
            "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(params.timeoutMs || 45000),
    });

    const contentType = res.headers.get("content-type") || "application/octet-stream";
    if (!res.ok) {
        const errText = await res.text().catch(() => "");
        throw new Error(`TTS HTTP ${res.status}: ${errText.slice(0, 300)}`);
    }

    if (contentType.includes("application/json")) {
        const text = await res.text();
        const parsed = JSON.parse(text) as Record<string, unknown>;
        const b64 =
            (typeof parsed.audio === "string" && parsed.audio) ||
            (typeof parsed.audio_base64 === "string" && parsed.audio_base64) ||
            "";
        if (!b64) {
            throw new Error("TTS JSON response does not contain audio base64 field");
        }
        const bytes = Buffer.from(b64, "base64");
        return {
            audioBytes: new Uint8Array(bytes),
            mimeType: params.format === "wav" ? "audio/wav" : "audio/mpeg",
        };
    }

    const ab = await res.arrayBuffer();
    return {
        audioBytes: new Uint8Array(ab),
        mimeType: contentType,
    };
}
