/**
 * GitHub Trending 仓库查询
 * 主路径：解析 github.com/trending HTML
 * 降级路径：使用 GitHub Search API
 */
import { fetchJsonByCurl, fetchTextByCurl, fetchJson } from "../../shared/request.js";
import { errToString, clamp } from "../../shared/text.js";
import { env, envAny } from "../../shared/env.js";

export type GithubTrendingItem = {
    repo: string;
    url: string;
    description: string;
    language: string;
    starsTotal: number | null;
    starsPeriod: number | null;
    since: "daily" | "weekly" | "monthly";
    source: "trending_html" | "search_api";
};

function cleanText(input: string, maxLen: number = 260): string {
    const text = (input || "")
        .replace(/<[^>]+>/g, " ")
        .replace(/&amp;/g, "&")
        .replace(/&lt;/g, "<")
        .replace(/&gt;/g, ">")
        .replace(/&quot;/g, "\"")
        .replace(/&#39;/g, "'")
        .replace(/&nbsp;/g, " ")
        .replace(/\s+/g, " ")
        .trim();
    if (text.length <= maxLen) {
        return text;
    }
    return `${text.slice(0, maxLen)}...`;
}

function isoDateDaysAgo(days: number): string {
    const d = new Date(Date.now() - Math.max(0, days) * 24 * 60 * 60 * 1000);
    return d.toISOString().slice(0, 10);
}

async function fetchGithubTrendingBySearchApi(params: {
    since: "daily" | "weekly" | "monthly";
    language?: string;
    limit: number;
}): Promise<GithubTrendingItem[]> {
    const since = params.since;
    const language = (params.language || "").trim();
    const limit = clamp(params.limit, 1, 20);
    const days = since === "daily" ? 1 : since === "monthly" ? 30 : 7;

    const queryParts = [`created:>=${isoDateDaysAgo(days)}`];
    if (language) {
        queryParts.push(`language:${language}`);
    }

    const url = new URL("https://api.github.com/search/repositories");
    url.searchParams.set("q", queryParts.join(" "));
    url.searchParams.set("sort", "stars");
    url.searchParams.set("order", "desc");
    url.searchParams.set("per_page", String(limit));

    const proxy = envAny([
        "GITHUB_TRENDING_PROXY",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "https_proxy",
        "http_proxy",
        "ALL_PROXY",
        "all_proxy",
    ]);
    const token = env("GITHUB_TOKEN");

    const headers: Record<string, string> = {
        Accept: "application/vnd.github+json",
        "User-Agent": "xiao-a-openclaw",
        "X-GitHub-Api-Version": "2022-11-28",
    };
    if (token) {
        headers.Authorization = `Bearer ${token}`;
    }

    const data = (await fetchJsonByCurl({
        url: url.toString(),
        timeoutSec: 20,
        proxy: proxy || undefined,
        headers,
    })) as {
        items?: Array<{
            full_name?: string;
            html_url?: string;
            description?: string | null;
            language?: string | null;
            stargazers_count?: number;
        }>;
        message?: string;
    };

    const items = Array.isArray(data.items) ? data.items : [];
    return items.slice(0, limit).map((it) => {
        const repo = String(it.full_name || "").trim();
        return {
            repo,
            url: String(it.html_url || `https://github.com/${repo}`),
            description: cleanText(it.description || "", 260),
            language: String(it.language || "").trim(),
            starsTotal: Number.isFinite(Number(it.stargazers_count)) ? Number(it.stargazers_count) : null,
            starsPeriod: null,
            since,
            source: "search_api" as const,
        };
    });
}

export async function fetchGithubTrending(params: {
    since: "daily" | "weekly" | "monthly";
    language?: string;
    limit: number;
}): Promise<GithubTrendingItem[]> {
    const since = params.since;
    const language = (params.language || "").trim().toLowerCase();
    const limit = clamp(params.limit, 1, 20);
    const langPath = language ? `/${encodeURIComponent(language)}` : "";
    const url = `https://github.com/trending${langPath}?since=${since}`;

    const proxy = envAny([
        "GITHUB_TRENDING_PROXY",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "https_proxy",
        "http_proxy",
        "ALL_PROXY",
        "all_proxy",
    ]);

    try {
        const html = await fetchTextByCurl({
            url,
            timeoutSec: 35,
            compressed: true,
            proxy: proxy || undefined,
            headers: {
                "User-Agent":
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                Accept: "text/html,application/xhtml+xml",
                Referer: "https://github.com/trending",
            },
        });

        const articleRegex = /<article[\s\S]*?<\/article>/g;
        const rows = (html.match(articleRegex) || [])
            .filter((row) => /Box-row/.test(row))
            .slice(0, Math.max(limit * 3, 20));

        const out: GithubTrendingItem[] = [];
        const seen = new Set<string>();
        for (const row of rows) {
            const repoMatch = row.match(/<h2[\s\S]*?<a[^>]*href="\/([^"?#]+)"/i);
            const repo = cleanText(repoMatch?.[1] || "", 120).replace(/\s+/g, "");
            if (!repo || !repo.includes("/") || seen.has(repo)) {
                continue;
            }

            const descMatch = row.match(/<p[^>]*>([\s\S]*?)<\/p>/i);
            const description = cleanText(descMatch?.[1] || "", 260);

            const langMatch = row.match(/itemprop="programmingLanguage"[^>]*>([\s\S]*?)<\/span>/i);
            const repoLang = cleanText(langMatch?.[1] || "", 60);

            const starTotalMatch = row.match(/href="\/[^"?#]+\/stargazers"[^>]*>\s*([\d,]+)\s*<\/a>/i);
            const starsTotal = starTotalMatch?.[1] ? Number(starTotalMatch[1].replace(/,/g, "")) : null;

            const starPeriodMatch = row.match(/([\d,]+)\s+stars?\s+(today|this week|this month)/i);
            const starsPeriod = starPeriodMatch?.[1] ? Number(starPeriodMatch[1].replace(/,/g, "")) : null;

            seen.add(repo);
            out.push({
                repo,
                url: `https://github.com/${repo}`,
                description,
                language: repoLang || "",
                starsTotal: Number.isFinite(starsTotal) ? starsTotal : null,
                starsPeriod: Number.isFinite(starsPeriod) ? starsPeriod : null,
                since,
                source: "trending_html",
            });

            if (out.length >= limit) {
                break;
            }
        }

        if (out.length > 0) {
            return out;
        }
    } catch {
        // fallback below
    }

    return await fetchGithubTrendingBySearchApi({
        since,
        language,
        limit,
    });
}
