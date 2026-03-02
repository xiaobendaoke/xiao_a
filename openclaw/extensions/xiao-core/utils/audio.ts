import { existsSync, promises as fs } from "node:fs";
import path from "node:path";
import { env } from "../../shared/env.js";
import { shorten } from "../../shared/text.js";

function resolveDashscopeApiKey(): string {
  const direct = env("DASHSCOPE_API_KEY");
  if (direct) return direct;
  const alt = env("QWEN_API_KEY");
  if (alt) return alt;
  return "";
}

export async function transcribeAudioPathForContext(audioPath: string): Promise<string | null> {
  const apiKey = resolveDashscopeApiKey();
  if (!apiKey) return null;
  const model = env("DASHSCOPE_ASR_MODEL") || "qwen3-asr-flash";
  const baseUrl = (env("DASHSCOPE_BASE_URL") || "https://dashscope.aliyuncs.com/compatible-mode/v1").replace(/\/$/, "");
  const absolutePath = path.resolve(audioPath || "");
  if (!existsSync(absolutePath)) {
    return null;
  }

  try {
    const bytes = await fs.readFile(absolutePath);
    if (!bytes || bytes.byteLength === 0) {
      return null;
    }

    const ext = path.extname(absolutePath).toLowerCase();
    const mimeType =
      ext === ".wav"
        ? "audio/wav"
        : ext === ".mp3"
        ? "audio/mpeg"
        : ext === ".ogg"
        ? "audio/ogg"
        : ext === ".m4a"
        ? "audio/mp4"
        : "application/octet-stream";

    const form = new FormData();
    form.set("model", model);
    form.set("file", new Blob([bytes], { type: mimeType }), path.basename(absolutePath) || "audio.wav");

    const res = await fetch(`${baseUrl}/audio/transcriptions`, {
      method: "POST",
      signal: AbortSignal.timeout(25000),
      headers: {
        Authorization: `Bearer ${apiKey}`,
      },
      body: form,
    });

    if (!res.ok) {
      return null;
    }

    const payload = (await res.json()) as Record<string, unknown>;
    const transcript =
      (typeof payload.text === "string" && payload.text.trim()) ||
      (typeof payload.transcript === "string" && payload.transcript.trim()) ||
      "";
    return transcript ? shorten(transcript, 500) : null;
  } catch {
    return null;
  }
}
