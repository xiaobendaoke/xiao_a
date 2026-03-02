import { promises as fs } from "node:fs";
import path from "node:path";
import { env } from "../../shared/env.js";
import { clamp } from "../../shared/text.js";
import { fetchBytes } from "../../shared/request.js";

export function extFromMime(mimeType: string): string {
    const mime = (mimeType || "").toLowerCase();
    if (mime.includes("mpeg") || mime.includes("mp3")) return "mp3";
    if (mime.includes("wav")) return "wav";
    if (mime.includes("ogg")) return "ogg";
    if (mime.includes("webm")) return "webm";
    if (mime.includes("aac")) return "aac";
    if (mime.includes("flac")) return "flac";
    if (mime.includes("m4a") || mime.includes("mp4")) return "m4a";
    return "bin";
}

export function mimeFromPath(filePath: string): string {
    const ext = path.extname(filePath).toLowerCase();
    if (ext === ".mp3") return "audio/mpeg";
    if (ext === ".wav") return "audio/wav";
    if (ext === ".ogg") return "audio/ogg";
    if (ext === ".webm") return "audio/webm";
    if (ext === ".aac") return "audio/aac";
    if (ext === ".flac") return "audio/flac";
    if (ext === ".m4a" || ext === ".mp4") return "audio/mp4";
    return "application/octet-stream";
}

export function mediaMaxBytes(): number {
    const mbRaw = env("XIAO_MEDIA_MAX_MB") || "20";
    const mb = clamp(Number(mbRaw), 1, 200);
    return Math.trunc(mb * 1024 * 1024);
}

export function parseBase64AudioInput(input: string): {
    bytes: Uint8Array;
    mimeType: string;
    filename: string;
} {
    const text = (input || "").trim();
    if (!text) {
        throw new Error("audioBase64 is empty");
    }

    const dataUrlMatch = text.match(/^data:([^;]+);base64,(.+)$/i);
    if (dataUrlMatch) {
        const mimeType = dataUrlMatch[1] || "application/octet-stream";
        const b64 = dataUrlMatch[2] || "";
        const bytes = Buffer.from(b64, "base64");
        const ext = extFromMime(mimeType);
        return {
            bytes: new Uint8Array(bytes),
            mimeType,
            filename: `audio.${ext}`,
        };
    }

    const bytes = Buffer.from(text, "base64");
    if (!bytes.length) {
        throw new Error("invalid base64 audio input");
    }
    return {
        bytes: new Uint8Array(bytes),
        mimeType: "application/octet-stream",
        filename: "audio.bin",
    };
}

export function parseBase64ImageInput(input: string): {
    bytes: Uint8Array;
    mimeType: string;
} {
    const text = (input || "").trim();
    if (!text) {
        throw new Error("invalid_input: imageUrl is empty");
    }
    const dataUrlMatch = text.match(/^data:([^;]+);base64,(.+)$/i);
    if (!dataUrlMatch?.[1] || !dataUrlMatch?.[2]) {
        throw new Error("invalid_input: imageUrl must be a valid image URL or data URL");
    }
    const mimeType = (dataUrlMatch[1] || "application/octet-stream").trim().toLowerCase();
    if (!mimeType.startsWith("image/")) {
        throw new Error(`unsupported_media_type: ${mimeType}`);
    }
    const bytes = Buffer.from(dataUrlMatch[2], "base64");
    if (!bytes.length) {
        throw new Error("invalid_input: image data is empty");
    }
    const maxBytes = mediaMaxBytes();
    if (bytes.byteLength > maxBytes) {
        throw new Error(`media_too_large: bytes=${bytes.byteLength} > max=${maxBytes}`);
    }
    return {
        bytes: new Uint8Array(bytes),
        mimeType,
    };
}

export async function resolveVisionImageInput(imageUrl: string, timeoutMs: number): Promise<{
    imageRef: string;
    source: "data_url" | "downloaded_url";
    mimeType: string;
    bytes: number;
}> {
    const raw = (imageUrl || "").trim();
    if (!raw) {
        throw new Error("invalid_input: imageUrl is required");
    }

    if (/^data:/i.test(raw)) {
        const parsed = parseBase64ImageInput(raw);
        return {
            imageRef: raw,
            source: "data_url",
            mimeType: parsed.mimeType,
            bytes: parsed.bytes.byteLength,
        };
    }

    let urlObj: URL;
    try {
        urlObj = new URL(raw);
    } catch {
        throw new Error("invalid_input: imageUrl is not a valid URL");
    }
    if (!/^https?:$/i.test(urlObj.protocol)) {
        throw new Error("invalid_input: imageUrl must use http/https");
    }

    const downloaded = await fetchBytes(raw, undefined, clamp(Math.trunc(timeoutMs * 0.7), 6000, 30000));
    const mimeType = (downloaded.contentType || "").toLowerCase();
    if (!mimeType.startsWith("image/")) {
        throw new Error(`unsupported_media_type: ${mimeType || "unknown"}`);
    }
    const dataUrl = `data:${mimeType};base64,${Buffer.from(downloaded.bytes).toString("base64")}`;
    return {
        imageRef: dataUrl,
        source: "downloaded_url",
        mimeType,
        bytes: downloaded.bytes.byteLength,
    };
}

export async function resolveAudioInput(params: {
    audioUrl?: string;
    audioBase64?: string;
    audioPath?: string;
}): Promise<{ bytes: Uint8Array; mimeType: string; filename: string }> {
    const audioUrl = (params.audioUrl || "").trim();
    const audioBase64 = (params.audioBase64 || "").trim();
    const audioPath = (params.audioPath || "").trim();

    if (!audioUrl && !audioBase64 && !audioPath) {
        throw new Error("audioUrl or audioBase64 or audioPath is required");
    }

    if (audioUrl) {
        const downloaded = await fetchBytes(audioUrl, undefined, 25000);
        const ext = extFromMime(downloaded.contentType);
        return {
            bytes: downloaded.bytes,
            mimeType: downloaded.contentType,
            filename: `audio.${ext}`,
        };
    }

    if (audioPath) {
        const absolutePath = path.resolve(audioPath);
        const st = await fs.stat(absolutePath);
        const maxBytes = mediaMaxBytes();
        if (st.size > maxBytes) {
            throw new Error(`media_too_large: file=${st.size} > max=${maxBytes}`);
        }
        const fileBytes = await fs.readFile(absolutePath);
        if (!fileBytes.byteLength) {
            throw new Error("audioPath points to empty file");
        }
        const mime = mimeFromPath(absolutePath);
        return {
            bytes: new Uint8Array(fileBytes),
            mimeType: mime,
            filename: path.basename(absolutePath) || `audio.${extFromMime(mime)}`,
        };
    }

    return parseBase64AudioInput(audioBase64);
}

