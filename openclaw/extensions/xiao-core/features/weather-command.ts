import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { shorten } from "../../shared/text.js";
import { fetchJson } from "../../shared/request.js";

export function inferCityFromInput(input: string): string | null {
  const text = (input || "").trim();
  if (!text) {
    return null;
  }

  const m1 = text.match(/(?:查|看|问|告诉我|知道|今天|明天|后天|现在)?([\p{Script=Han}]{2,8})(?:天气|气温|温度|下雨|降雨)/u);
  if (m1?.[1]) {
    return m1[1];
  }

  const m2 = text.match(/([\p{Script=Han}]{2,8})(?:今天|明天|后天)?(?:冷不冷|热不热)/u);
  if (m2?.[1]) {
    return m2[1];
  }

  return null;
}

export function weatherCodeToText(code: number): string {
  const c = Math.trunc(code);
  if (c === 0) return "晴";
  if (c === 1 || c === 2 || c === 3) return "多云";
  if (c === 45 || c === 48) return "雾";
  if (c >= 51 && c <= 57) return "毛毛雨";
  if (c >= 61 && c <= 67) return "雨";
  if (c >= 71 && c <= 77) return "雪";
  if (c >= 80 && c <= 82) return "阵雨";
  if (c >= 95 && c <= 99) return "雷雨";
  return "未知";
}

export async function fetchWeatherSummary(city: string): Promise<string | null> {
  try {
    const geoUrl = new URL("https://geocoding-api.open-meteo.com/v1/search");
    geoUrl.searchParams.set("name", city);
    geoUrl.searchParams.set("count", "1");
    geoUrl.searchParams.set("language", "zh");
    geoUrl.searchParams.set("format", "json");

    const geo = (await fetchJson(geoUrl.toString(), undefined, 7000)) as {
      results?: Array<{ name?: string; country?: string; latitude?: number; longitude?: number }>;
    };
    const loc = geo.results?.[0];
    if (!loc || typeof loc.latitude !== "number" || typeof loc.longitude !== "number") {
      return null;
    }

    const forecastUrl = new URL("https://api.open-meteo.com/v1/forecast");
    forecastUrl.searchParams.set("latitude", String(loc.latitude));
    forecastUrl.searchParams.set("longitude", String(loc.longitude));
    forecastUrl.searchParams.set("current", "temperature_2m,apparent_temperature,weather_code,wind_speed_10m");
    forecastUrl.searchParams.set(
      "daily",
      "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
    );
    forecastUrl.searchParams.set("timezone", "Asia/Shanghai");

    const fc = (await fetchJson(forecastUrl.toString(), undefined, 9000)) as {
      current?: {
        temperature_2m?: number;
        apparent_temperature?: number;
        weather_code?: number;
        wind_speed_10m?: number;
      };
      daily?: {
        weather_code?: number[];
        temperature_2m_max?: number[];
        temperature_2m_min?: number[];
        precipitation_probability_max?: number[];
      };
    };

    const cur = fc.current || {};
    const daily = fc.daily || {};
    const todayCode = Number(daily.weather_code?.[0] ?? cur.weather_code ?? -1);
    const todayMax = Number(daily.temperature_2m_max?.[0] ?? NaN);
    const todayMin = Number(daily.temperature_2m_min?.[0] ?? NaN);
    const pop = Number(daily.precipitation_probability_max?.[0] ?? NaN);
    const nowTemp = Number(cur.temperature_2m ?? NaN);

    const pieces: string[] = [];
    pieces.push(`城市=${loc.name || city}`);
    if (Number.isFinite(nowTemp)) {
      pieces.push(`当前温度=${nowTemp.toFixed(1)}C`);
    }
    if (Number.isFinite(todayMin) && Number.isFinite(todayMax)) {
      pieces.push(`今日温度=${todayMin.toFixed(1)}~${todayMax.toFixed(1)}C`);
    }
    if (Number.isFinite(todayCode) && todayCode >= 0) {
      pieces.push(`天气=${weatherCodeToText(todayCode)}(code=${todayCode})`);
    }
    if (Number.isFinite(pop)) {
      pieces.push(`降雨概率=${Math.max(0, Math.round(pop))}%`);
    }
    return pieces.join("；");
  } catch {
    return null;
  }
}

export function registerXiaoWeatherCommand(api: OpenClawPluginApi): void {
  // 注册 /xiao-weather 命令，用于给指定城市快速查阅当日天气概况
  api.registerCommand({
    name: "xiao-weather",
    description: "Direct weather query without chat-LLM. Usage: /xiao-weather <city>",
    acceptsArgs: true,
    handler: async (ctx) => {
      const args = (ctx.args || "").trim();

      // 利用正则提取市名称或者直接进行长度截断容错
      const city = inferCityFromInput(args) || shorten(args, 20).trim();
      if (!city) {
        return { text: "飞飞，告诉我想查哪个城市呀～例如：/xiao-weather 绵阳" };
      }

      // 调用封装好的查询接口抓取对应城市天气摘要描述
      const summary = await fetchWeatherSummary(city);
      if (!summary) {
        return { text: `飞飞，我这会儿没拉到 ${city} 的天气数据，稍后我再帮你查一次。` };
      }

      // 拼接给用户的友好返回文案
      return {
        text: [
          `飞飞，我查到 ${city} 啦：${summary}`,
          "要不要我再按这个天气帮你给个出门建议？",
        ].join("\n"),
      };
    },
  });
}
