import { env } from "../../shared/env.js";
import { fetchJson } from "../../shared/request.js";

const TMDB_BASE = "https://api.themoviedb.org/3";

export async function recommendMovies(query: string, limit: number = 5): Promise<Record<string, unknown>> {
  const key = env("TMDB_API_KEY");
  if (!key) return { ok: false, error: "missing_env", missing: ["TMDB_API_KEY"] };
  const url = new URL(`${TMDB_BASE}/search/movie`);
  url.searchParams.set("api_key", key);
  url.searchParams.set("language", "zh-CN");
  url.searchParams.set("query", query || "电影");
  url.searchParams.set("page", "1");
  const data = (await fetchJson(url.toString(), undefined, 12000)) as { results?: Array<Record<string, unknown>> };
  const items = (data.results || []).slice(0, limit).map((m) => ({
    title: String(m.title || ""),
    overview: String(m.overview || "").slice(0, 120),
    rating: Number(m.vote_average || 0),
    releaseDate: String(m.release_date || ""),
  }));
  return { ok: true, provider: "tmdb", items };
}
