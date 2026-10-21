"""Private, durable numeric ledger outside the public Git checkout.

An explicitly seeded, previously published snapshot covers calls through cutoff.
Only calls strictly after cutoff are added, keyed by SHA-256 of their message ID.
No message content, original IDs or project paths are stored here or exported.
"""

import hashlib
import json
import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sync_ai_usage import ROOT

HISTORY_PATH = ROOT.parent / ".ai-usage-state" / "claude-history.json"
COUNTERS = {"model_calls", "input_tokens", "output_tokens", "cache_read_tokens", "total_tokens"}


def stamp(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("History timestamp lacks timezone")
    return result


def validate_state(state):
    if set(state) != {"schema_version", "cutoff", "baseline", "source_sha256", "records"} or state["schema_version"] != 1:
        raise ValueError("Invalid private history schema")
    cutoff = stamp(state["cutoff"])
    if len(state["source_sha256"]) != 64:
        raise ValueError("Missing historical snapshot provenance")
    dates = set()
    for day in state["baseline"]:
        if set(day) != COUNTERS | {"date"} or day["date"] in dates:
            raise ValueError("Invalid or duplicate historical day")
        if date.fromisoformat(day["date"]) > cutoff.astimezone(ZoneInfo("Asia/Singapore")).date():
            raise ValueError("Historical day exceeds cutoff")
        dates.add(day["date"])
        if any(type(day[k]) is not int or day[k] < 0 for k in COUNTERS):
            raise ValueError("Invalid historical counter")
        if day["total_tokens"] != day["input_tokens"] + day["output_tokens"] or day["cache_read_tokens"] > day["input_tokens"]:
            raise ValueError("Historical counters disagree")
    for key, record in state["records"].items():
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("Invalid private request digest")
        if set(record) != {"at", "input", "output", "cached"} or stamp(record["at"]) <= cutoff:
            raise ValueError("Private request overlaps historical baseline")
        if any(type(record[k]) is not int or record[k] < 0 for k in ("input", "output", "cached")) or record["cached"] > record["input"]:
            raise ValueError("Invalid retained request counters")
    return state


def seed_history(snapshot):
    from usage_cards import validate
    from sync_ai_usage import aggregate
    validate(snapshot)
    baseline = snapshot["providers"]["claude"]
    cutoff = stamp(snapshot["generated_at"])
    rebuilt = aggregate([], cutoff, baseline["daily"])
    if rebuilt != baseline:
        raise ValueError("Historical snapshot does not match its daily counters")
    return validate_state({"schema_version": 1, "cutoff": cutoff.isoformat(),
        "baseline": baseline["daily"], "records": {},
        "source_sha256": hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()})


def retain(state, observed, now):
    from sync_ai_usage import merge
    validate_state(state)
    cutoff = stamp(state["cutoff"])
    if cutoff > now:
        raise ValueError("Historical cutoff is in the future")
    records = {key: {**value, "at": stamp(value["at"])} for key, value in state["records"].items()}
    if any(record["at"] > now for record in records.values()):
        raise ValueError("Retained history is ahead of the system clock")
    for identifier, record in observed.items():
        # Never add restored/copied old transcripts on top of the sealed baseline.
        if cutoff < record["at"] <= now:
            key = hashlib.sha256(identifier.encode()).hexdigest()
            merge(records, key, record)
    state["records"] = {key: {**value, "at": value["at"].isoformat()} for key, value in records.items()}
    validate_state(state)
    return records.values()


def load_history(path):
    if not path.exists():
        return None  # Original history-loss guard still stops publication.
    try:
        return validate_state(json.loads(path.read_text()))
    except (ValueError, KeyError, TypeError):
        raise ValueError("Private usage history is invalid; restore its local backup") from None


def save_history(path, state):
    validate_state(state)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = json.dumps(state, sort_keys=True) + "\n"
    # Keep the prior valid ledger as a recoverable local backup, never in Git.
    if path.exists():
        previous = load_history(path)
        _atomic_write(path.with_suffix(".json.bak"), json.dumps(previous, sort_keys=True) + "\n")
    _atomic_write(path, payload)


def _atomic_write(path, payload):
    descriptor, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
