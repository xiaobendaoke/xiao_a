import { fetchJson } from "../../shared/request.js";

export function extractMusicInfo(url: string): { platform: string; songId: string } | null {
  const text = (url || "").trim();
  let m = text.match(/music\.163\.com.*?id=(\d+)/i);
  if (m?.[1]) return { platform: "netease", songId: m[1] };
  m = text.match(/y\.qq\.com.*?songmid=([^&]+)/i);
  if (m?.[1]) return { platform: "qq", songId: m[1] };
  return null;
}

export async function resolveMusic(url: string): Promise<Record<string, unknown>> {
  const info = extractMusicInfo(url);
  if (!info) return { ok: false, error: "unsupported_url" };
  if (info.platform === "netease") {
    const api = new URL("https://netease-cloud-music-api-five-roan-25.vercel.app/song/detail");
    api.searchParams.set("ids", info.songId);
    const data = (await fetchJson(api.toString(), undefined, 12000)) as { songs?: Array<Record<string, unknown>>; code?: number };
    const song = data.songs?.[0];
    if (!song) return { ok: false, error: "song_not_found", platform: "netease" };
    const artists = Array.isArray(song.ar) ? song.ar.map((x: Record<string, unknown>) => String(x.name || "")).filter(Boolean) : [];
    return {
      ok: true,
      platform: "netease",
      songId: info.songId,
      name: String(song.name || ""),
      artists,
      album: String((song.al as Record<string, unknown>)?.name || ""),
      durationSec: Math.trunc(Number(song.dt || 0) / 1000),
    };
  }
  return { ok: true, platform: "qq", songId: info.songId };
}
