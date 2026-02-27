#!/usr/bin/env python3
"""Migrate xiao_a sqlite mood/profile into xiao-emotion state.json.

Usage:
  python3 migrate_from_xiao_a.py \
    --db /root/xiao_a/bot/plugins/companion_core/data.db \
    --out /root/.openclaw/xiao-emotion/state.json \
    --prefix qqbot
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from pathlib import Path


def clamp(n: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, n))


def to_user_key(prefix: str, raw_user_id: str) -> str:
    uid = (raw_user_id or "").strip()
    if not uid:
        return ""
    if not prefix:
        return uid
    if ":" in uid:
        return uid
    return f"{prefix}:{uid}"


def load_json(path: Path) -> dict:
    if not path.exists():
        return {"moods": {}, "profiles": {}}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"moods": {}, "profiles": {}}
        moods = data.get("moods") if isinstance(data.get("moods"), dict) else {}
        profiles = data.get("profiles") if isinstance(data.get("profiles"), dict) else {}
        return {"moods": moods, "profiles": profiles}
    except Exception:
        return {"moods": {}, "profiles": {}}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="Path to xiao_a sqlite db")
    parser.add_argument("--out", required=True, help="Path to xiao-emotion state.json")
    parser.add_argument("--prefix", default="qqbot", help="User key prefix (default: qqbot)")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    args = parser.parse_args()

    db_path = Path(args.db)
    out_path = Path(args.out)
    prefix = (args.prefix or "").strip()

    if not db_path.exists():
        raise SystemExit(f"db not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    mood_rows = cur.execute("SELECT user_id, mood_value FROM user_mood").fetchall()
    profile_rows = cur.execute("SELECT user_id, key, value FROM user_profile").fetchall()

    now_ms = int(time.time() * 1000)
    profile_map: dict[str, dict[str, str]] = {}
    mood_updated_ts_sec: dict[str, float] = {}

    for row in profile_rows:
        uid = (row["user_id"] or "").strip()
        key = (row["key"] or "").strip()
        val = "" if row["value"] is None else str(row["value"])
        user_key = to_user_key(prefix, uid)
        if not user_key:
            continue

        if key == "mood_updated_ts":
            try:
                mood_updated_ts_sec[user_key] = float(val)
            except Exception:
                pass
            continue

        profile = profile_map.setdefault(user_key, {})
        profile[key] = val

    out_data = load_json(out_path)
    out_moods = out_data.setdefault("moods", {})
    out_profiles = out_data.setdefault("profiles", {})

    migrated_mood = 0
    migrated_profile_users = 0

    for row in mood_rows:
        uid = (row["user_id"] or "").strip()
        user_key = to_user_key(prefix, uid)
        if not user_key:
            continue

        mood_val = 0
        try:
            mood_val = int(row["mood_value"])
        except Exception:
            mood_val = 0
        mood_val = clamp(mood_val, -100, 100)

        ts_sec = mood_updated_ts_sec.get(user_key)
        if ts_sec is not None and ts_sec > 0:
            updated_at = int(ts_sec * 1000)
        else:
            updated_at = now_ms

        out_moods[user_key] = {
            "value": mood_val,
            "updatedAt": updated_at,
        }
        migrated_mood += 1

    for user_key, profile in profile_map.items():
        if not profile:
            continue
        existing = out_profiles.get(user_key)
        if not isinstance(existing, dict):
            existing = {}
        existing.update(profile)
        out_profiles[user_key] = existing
        migrated_profile_users += 1

    if args.dry_run:
        print(
            json.dumps(
                {
                    "ok": True,
                    "dryRun": True,
                    "migratedMoodRows": migrated_mood,
                    "migratedProfileUsers": migrated_profile_users,
                    "outputPath": str(out_path),
                },
                ensure_ascii=False,
            )
        )
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        backup = out_path.with_suffix(out_path.suffix + f".bak.{int(time.time())}")
        backup.write_bytes(out_path.read_bytes())

    with out_path.open("w", encoding="utf-8") as f:
        json.dump(out_data, f, ensure_ascii=False, indent=2)

    print(
        json.dumps(
            {
                "ok": True,
                "migratedMoodRows": migrated_mood,
                "migratedProfileUsers": migrated_profile_users,
                "outputPath": str(out_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
