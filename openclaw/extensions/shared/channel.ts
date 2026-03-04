import { env } from "./env.js";

const DEFAULT_ALLOWED_CHANNELS = ["qqbot"];
const DEFAULT_PRIMARY_CHANNEL = "qqbot";

function splitCsv(raw: string): string[] {
  return (raw || "")
    .split(",")
    .map((x) => x.trim().toLowerCase())
    .filter((x) => x.length > 0);
}

export function getAllowedChannels(): string[] {
  const explicit = splitCsv(env("XIAO_ALLOWED_CHANNELS"));
  if (explicit.length > 0) {
    return Array.from(new Set(explicit));
  }
  return DEFAULT_ALLOWED_CHANNELS.slice();
}

export function getPrimaryChannel(): string {
  const primary = env("XIAO_PRIMARY_CHANNEL").trim().toLowerCase();
  if (primary) {
    return primary;
  }
  return DEFAULT_PRIMARY_CHANNEL;
}

export function isChannelAllowed(channelId: string | undefined | null): boolean {
  const channel = (channelId || "").trim().toLowerCase();
  if (!channel) {
    return false;
  }
  return getAllowedChannels().includes(channel);
}

export function assertAllowedChannel(channelId: string | undefined | null): { ok: true } | { ok: false; reason: string } {
  const channel = (channelId || "").trim().toLowerCase();
  if (!channel) {
    return { ok: false, reason: "channel missing" };
  }
  const allowed = getAllowedChannels();
  if (!allowed.includes(channel)) {
    return {
      ok: false,
      reason: `channel ${channel} is not allowed by XIAO_ALLOWED_CHANNELS=${allowed.join(",")}`,
    };
  }
  return { ok: true };
}
