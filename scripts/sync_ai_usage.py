"""Aggregate local Codex and Claude Code token counters; export no chat content.

Only request IDs (for in-memory deduplication), timestamps and numeric usage
counters are selected. IDs, prompts, responses, filenames, paths and credentials
are never included in the public output. No provider API or model is called.
"""

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SGT = ZoneInfo("Asia/Singapore")


def counter(usage, key):
    value = usage.get(key, 0)
    if type(value) is not int or value < 0:
        raise ValueError("Invalid token counter")
    return value


def normalize(entry, provider):
    if provider == "codex":
        if entry.get("type") != "token_usage_record":
            return None
        payload = entry.get("payload") or {}
        identifier, usage = payload.get("response_id"), payload.get("usage") or {}
        # input_tokens already includes cache hits. Reasoning is part of output.
        inp, out = counter(usage, "input_tokens"), counter(usage, "output_tokens")
        cached = counter(usage, "cached_input_tokens")
        if "total_tokens" in usage and counter(usage, "total_tokens") != inp + out:
            raise ValueError("Codex total does not match input + output")
    else:
        if entry.get("type") != "assistant":
            return None
        message = entry.get("message") or {}
        identifier, usage = message.get("id"), message.get("usage") or {}
        if not usage:
            return None
        # Anthropic separates uncached input, cache reads and cache creation.
        cached = counter(usage, "cache_read_input_tokens")
        inp = counter(usage, "input_tokens") + cached + counter(usage, "cache_creation_input_tokens")
        out = counter(usage, "output_tokens")
    if not isinstance(identifier, str) or not identifier or inp + out == 0:
        return None
    if cached > inp:
        raise ValueError("Cache reads exceed total input")
    stamp = datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Timestamp lacks timezone")
    return identifier, {"at": stamp, "input": inp, "output": out, "cached": cached}


def merge(records, identifier, record):
    previous = records.get(identifier)
    if previous:
        # A streaming response or copied transcript may repeat the same ID.
        record = {"at": min(previous["at"], record["at"]), **{
            key: max(previous[key], record[key]) for key in ("input", "output", "cached")
        }}
    records[identifier] = record


def collect(paths, provider, now):
    records, audit = {}, Counter()
    seen_files = set()
    for root in paths:
        if not root.exists():
            continue
        for path in root.rglob("*.jsonl"):
            resolved = path.resolve()
            if resolved in seen_files:
                continue
            seen_files.add(resolved)
            audit["files"] += 1
            with path.open(encoding="utf-8", errors="replace") as source:
                for line in source:
                    marker = '"token_usage_record"' if provider == "codex" else '"usage"'
                    if marker not in line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        audit["malformed_lines"] += 1
                        continue
                    try:
                        item = normalize(entry, provider)
                    except (ValueError, KeyError, TypeError):
                        # Fail rather than publish a plausible but wrong total.
                        raise ValueError(f"Invalid {provider} usage record; no snapshot written") from None
                    if item is None:
                        continue
                    identifier, record = item
                    if record["at"] > now:
                        continue
                    audit["usage_records"] += 1
                    if identifier in records:
                        audit["duplicates"] += 1
                    merge(records, identifier, record)
    audit["unique_model_calls"] = len(records)
    return records.values(), dict(audit)


def aggregate(records, now):
    today = now.astimezone(SGT).date()
    start = today - timedelta(days=27)
    days = defaultdict(lambda: Counter())
    totals = Counter()
    for record in records:
        day = record["at"].astimezone(SGT).date()
        for bucket in (totals, days[day]):
            bucket["model_calls"] += 1
            bucket["input_tokens"] += record["input"]
            bucket["output_tokens"] += record["output"]
            bucket["cache_read_tokens"] += record["cached"]
            bucket["total_tokens"] += record["input"] + record["output"]
    recent = Counter()
    for day, values in days.items():
        if start <= day <= today:
            recent.update(values)
            recent["active_days"] += 1
    keys = ("total_tokens", "input_tokens", "output_tokens", "cache_read_tokens", "model_calls")
    return {
        "first_recorded_date": min(days).isoformat() if days else None,
        "last_recorded_date": max(days).isoformat() if days else None,
        "totals": {key: totals[key] for key in keys},
        "last_28_days": {
            "start_date": start.isoformat(), "end_date": today.isoformat(),
            **{key: recent[key] for key in keys},
            "active_days": recent["active_days"],
            "average_tokens_per_week": recent["total_tokens"] / 4,
            "model_calls_per_active_day": round(recent["model_calls"] / recent["active_days"], 1) if recent["active_days"] else 0,
        },
        "daily": [{"date": day.isoformat(), **{key: values[key] for key in keys}}
                  for day, values in sorted(days.items())],
    }


def snapshot(home, now):
    providers = {
        "codex": [home / ".codex/sessions", home / ".codex/archived_sessions"],
        "claude": [home / ".claude/projects"],
    }
    result = {"schema_version": 2, "generated_at": now.isoformat(), "timezone": "Asia/Singapore", "providers": {}}
    audit = {}
    for provider, paths in providers.items():
        records, audit[provider] = collect(paths, provider, now)
        result["providers"][provider] = aggregate(records, now)
    return result, audit


def write_snapshot(data):
    from usage_cards import render_usage, validate
    validate(data)
    (ROOT / "data").mkdir(exist_ok=True)
    render_usage(data)
    (ROOT / "data/ai-usage.json").write_text(json.dumps(data, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home-dir", type=Path, default=Path.home(), help="Read local counters under this home directory")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    data, audit = snapshot(args.home_dir, now)
    write_snapshot(data)
    print(json.dumps({"aggregation_audit": audit, "public_totals": {
        provider: stats["totals"] for provider, stats in data["providers"].items()
    }}, indent=2))
