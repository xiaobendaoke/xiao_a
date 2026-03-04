#!/usr/bin/env python3
import json
import re
import time
from pathlib import Path
from urllib import request, error


ROOT = Path("/root/xiao_a")
DOCS = ROOT / "docs"
MODELS_FILE = DOCS / "openrouter_free_text_only_models.json"
OUT_JSON = DOCS / "openrouter_text_benchmark_results.json"
OUT_CSV = DOCS / "openrouter_text_benchmark_ranking.csv"


def load_openrouter_key() -> str:
    for p in [ROOT / ".env", Path.home() / ".openclaw" / ".env"]:
        if not p.exists():
            continue
        for line in p.read_text(errors="ignore").splitlines():
            if line.startswith("OPENROUTER_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


def call_model(api_key: str, model: str, prompt: str, max_tokens: int = 120, timeout_sec: int = 20) -> tuple[str, int, str]:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    req = request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://localhost",
            "X-Title": "xiao_a_openrouter_benchmark",
        },
        method="POST",
    )
    start = time.time()
    try:
        with request.urlopen(req, timeout=timeout_sec) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
            latency_ms = int((time.time() - start) * 1000)
            obj = json.loads(body)
            text = (
                obj.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            return text.strip(), latency_ms, ""
    except error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", errors="ignore")
        except Exception:
            detail = str(e)
        return "", int((time.time() - start) * 1000), f"http_{e.code}:{detail[:180]}"
    except Exception as e:
        return "", int((time.time() - start) * 1000), f"err:{str(e)[:180]}"


def grade(task_id: str, output: str) -> float:
    t = output.strip()
    if task_id == "math":
        return 1.0 if t == "2036" else 0.0
    if task_id == "count_a":
        return 1.0 if t == "3" else 0.0
    if task_id == "extract":
        return 1.0 if t == "2026-03-03|上海" else 0.0
    if task_id == "json":
        try:
            obj = json.loads(t)
            if (
                obj.get("name") == "xiao"
                and obj.get("age") == 3
                and obj.get("skills") == ["chat", "memory"]
            ):
                return 1.0
        except Exception:
            return 0.0
        return 0.0
    if task_id == "reverse":
        return 1.0 if t.lower() == "desserts" else 0.0
    if task_id == "style":
        if "小a" not in t:
            return 0.0
        if len(t) > 12:
            return 0.0
        if re.search(r"[，。！？,.!?;；:：\"'“”‘’()（）\\[\\]{}<>😀-🙏]", t):
            return 0.0
        return 1.0
    return 0.0


def main() -> None:
    api_key = load_openrouter_key()
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY not found in /root/xiao_a/.env or ~/.openclaw/.env")
    models = json.loads(MODELS_FILE.read_text())

    tasks = [
        {
            "id": "math",
            "prompt": "只输出数字，不要解释：125*17-89 = ?",
            "max_tokens": 16,
        },
        {
            "id": "count_a",
            "prompt": "字符串 banana 中字母 a 的个数，只输出数字。",
            "max_tokens": 8,
        },
        {
            "id": "extract",
            "prompt": "从句子里提取日期和城市，并按 yyyy-mm-dd|城市 输出，不要解释：我将于2026年3月3日去上海出差。",
            "max_tokens": 24,
        },
        {
            "id": "json",
            "prompt": "只输出JSON，不要markdown：name=xiao, age=3, skills=['chat','memory']。",
            "max_tokens": 80,
        },
        {
            "id": "reverse",
            "prompt": "把英文单词 stressed 倒序输出，只输出结果。",
            "max_tokens": 16,
        },
        {
            "id": "style",
            "prompt": "写一句早安问候：必须包含“小a”，12字以内，不要任何标点，不要emoji。",
            "max_tokens": 40,
        },
    ]

    results = []
    for m in models:
        model_id = m["id"]
        per_task = []
        fail_count = 0
        for task in tasks:
            text = ""
            latency = 0
            err = ""
            # retry once for transient errors
            for _ in range(2):
                text, latency, err = call_model(
                    api_key,
                    model_id,
                    task["prompt"],
                    task["max_tokens"],
                    timeout_sec=20,
                )
                if not err:
                    break
                time.sleep(0.4)
            if err:
                fail_count += 1
            score = grade(task["id"], text) if not err else 0.0
            per_task.append(
                {
                    "task_id": task["id"],
                    "output": text,
                    "latency_ms": latency,
                    "error": err,
                    "score": score,
                }
            )
        avg_latency = int(sum(x["latency_ms"] for x in per_task) / len(per_task))
        accuracy = sum(x["score"] for x in per_task) / len(per_task)
        # composite score: accuracy 85%, reliability 10%, latency 5%
        reliability = 1.0 - (fail_count / len(tasks))
        latency_score = max(0.0, min(1.0, 1.0 - (avg_latency / 12000)))
        final_score = 0.85 * accuracy + 0.10 * reliability + 0.05 * latency_score
        results.append(
            {
                "model": model_id,
                "accuracy": round(accuracy, 4),
                "reliability": round(reliability, 4),
                "avg_latency_ms": avg_latency,
                "final_score": round(final_score, 4),
                "tasks": per_task,
            }
        )
        print(
            f"[{len(results):02d}/{len(models)}] {model_id} "
            f"score={round(final_score,4)} acc={round(accuracy,4)} rel={round(reliability,4)} "
            f"lat={avg_latency}ms",
            flush=True,
        )

    results.sort(key=lambda x: x["final_score"], reverse=True)
    OUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2))

    with OUT_CSV.open("w", encoding="utf-8") as f:
        f.write("rank,model,final_score,accuracy,reliability,avg_latency_ms\n")
        for i, r in enumerate(results, 1):
            f.write(
                f"{i},{r['model']},{r['final_score']},{r['accuracy']},{r['reliability']},{r['avg_latency_ms']}\n"
            )

    print(f"saved_json={OUT_JSON}")
    print(f"saved_csv={OUT_CSV}")
    print(f"models_tested={len(results)}")
    if results:
        top = results[0]
        print(
            f"best_model={top['model']} score={top['final_score']} accuracy={top['accuracy']} latency_ms={top['avg_latency_ms']}"
        )


if __name__ == "__main__":
    main()
