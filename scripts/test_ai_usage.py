import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from pathlib import Path

from sync_ai_usage import aggregate, collect, merge, normalize, snapshot, zstd_command
from usage_cards import validate
from publish_ai_usage import guard_history
from usage_history import seed_history, retain, load_history, save_history


class WorkloadAccounting(unittest.TestCase):
    def test_codex_does_not_double_count_cache_or_reasoning(self):
        _, result = normalize({"type": "token_usage_record", "timestamp": "2026-10-20T01:00:00Z", "payload": {
            "response_id": "response-a", "usage": {"input_tokens": 100, "cached_input_tokens": 80,
            "output_tokens": 20, "reasoning_output_tokens": 10, "total_tokens": 120}}}, "codex")
        self.assertEqual((result["input"], result["output"]), (100, 20))

    def test_claude_counts_three_disjoint_input_categories_once(self):
        _, result = normalize({"type": "assistant", "timestamp": "2026-10-20T01:00:00Z", "message": {
            "id": "message-a", "usage": {"input_tokens": 5, "cache_read_input_tokens": 70,
            "cache_creation_input_tokens": 25, "output_tokens": 20,
            "output_tokens_details": {"thinking_tokens": 10}, "iterations": [{"output_tokens": 20}]}}}, "claude")
        self.assertEqual(result["input"] + result["output"], 120)

    def test_streaming_and_copied_transcripts_are_deduplicated_globally(self):
        entry = {"type": "assistant", "timestamp": "2026-10-20T01:00:00Z", "message": {
            "id": "same-response", "content": "PRIVATE_SENTINEL", "usage": {"input_tokens": 100, "output_tokens": 2}}}
        now = datetime(2026, 10, 20, 4, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "first.jsonl").write_text(json.dumps(entry) + "\n")
            entry["message"]["usage"]["output_tokens"] = 20
            (root / "copy.jsonl").write_text(json.dumps(entry) + "\n")
            records, audit = collect([root, root], "claude", now)
            result = aggregate(records, now)
        self.assertEqual(audit["unique_model_calls"], 1)
        self.assertEqual(result["totals"]["total_tokens"], 120)
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(result))
        self.assertNotIn("same-response", json.dumps(result))

    def test_singapore_window_and_inactive_dates(self):
        now = datetime(2026, 10, 20, 4, tzinfo=timezone.utc)
        records = [{"at": datetime.fromisoformat(stamp), "input": 100, "output": 20, "cached": 80}
                   for stamp in ("2026-09-22T15:59:00+00:00", "2026-09-22T16:01:00+00:00", "2026-10-20T03:00:00+00:00")]
        result = aggregate(records, now)
        self.assertEqual(result["totals"]["total_tokens"], 360)
        self.assertEqual(result["last_28_days"]["start_date"], "2026-09-23")
        self.assertEqual(result["last_28_days"]["average_tokens_per_week"], 60)
        self.assertEqual(result["last_28_days"]["active_days"], 2)

    def test_invalid_counters_fail_instead_of_publishing_wrong_totals(self):
        with self.assertRaises(ValueError):
            normalize({"type": "token_usage_record", "payload": {"usage": {"input_tokens": -1}}}, "codex")

    def test_public_schema_rejects_identity_or_content_fields(self):
        now = datetime(2026, 10, 20, 4, tzinfo=timezone.utc)
        stats = aggregate([], now)
        data = {"schema_version": 2, "generated_at": now.isoformat(), "timezone": "Asia/Singapore",
                "providers": {"codex": stats, "claude": stats}}
        validate(data)
        data["account_id"] = "PRIVATE_SENTINEL"
        with self.assertRaises(ValueError):
            validate(data)

    def test_automatic_publication_rejects_missing_local_logs(self):
        now = datetime(2026, 10, 20, 4, tzinfo=timezone.utc)
        stats = aggregate([], now)
        data = {"schema_version": 2, "generated_at": now.isoformat(), "timezone": "Asia/Singapore",
                "providers": {"codex": stats, "claude": stats}}
        with self.assertRaises(ValueError):
            guard_history(data, data, {"codex": {}, "claude": {}})

    def test_automatic_publication_rejects_lost_days_even_if_total_increases(self):
        now = datetime(2026, 10, 20, 4, tzinfo=timezone.utc)
        old = aggregate([{"at": datetime(2026, 10, 19, 4, tzinfo=timezone.utc), "input": 100, "output": 20, "cached": 80}], now)
        new = aggregate([{"at": now, "input": 1000, "output": 200, "cached": 800}], now)
        base = {"schema_version": 2, "generated_at": now.isoformat(), "timezone": "Asia/Singapore"}
        previous = {**base, "providers": {"codex": old, "claude": old}}
        current = {**base, "providers": {"codex": new, "claude": new}}
        audit = {p: {"files": 1, "unique_model_calls": 1} for p in ("codex", "claude")}
        with self.assertRaises(ValueError):
            guard_history(current, previous, audit)

    def history_fixture(self):
        cutoff = datetime(2026, 10, 20, 4, tzinfo=timezone.utc)
        record = {"at": cutoff, "input": 100, "output": 20, "cached": 80}
        stats = aggregate([record], cutoff)
        data = {"schema_version": 2, "generated_at": cutoff.isoformat(), "timezone": "Asia/Singapore",
                "providers": {"codex": stats, "claude": stats}}
        return cutoff, record, data, seed_history(data)

    def test_ledger_recovers_baseline_without_counting_restored_old_transcripts(self):
        cutoff, old, data, state = self.history_fixture()
        later = datetime(2026, 10, 20, 5, tzinfo=timezone.utc)
        new = {**old, "at": later}
        records = retain(state, {"old": old, "new": new, "copied-old": old}, later)
        current = aggregate(records, later, state["baseline"])
        self.assertEqual(current["totals"]["total_tokens"], 240)
        self.assertEqual(current["totals"]["model_calls"], 2)
        self.assertEqual(current["daily"][0]["total_tokens"], 240)

    def test_ledger_survives_deleted_logs_and_streaming_updates_without_double_counting(self):
        cutoff, old, data, state = self.history_fixture()
        later = datetime(2026, 10, 21, 5, tzinfo=timezone.utc)
        new = {**old, "at": later}
        retain(state, {"new": new}, later)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            save_history(path, state)
            state = load_history(path)
            result = aggregate(retain(state, {}, later), later, state["baseline"])
            self.assertEqual(result["totals"]["total_tokens"], 240)
            updated = {**new, "output": 30}
            result = aggregate(retain(state, {"new": updated}, later), later, state["baseline"])
            self.assertEqual(result["totals"]["total_tokens"], 250)
            self.assertEqual(result["totals"]["model_calls"], 2)
            self.assertEqual(result["last_28_days"]["average_tokens_per_week"], 62.5)
            save_history(path, state)
            self.assertTrue(path.with_suffix('.json.bak').exists())
            self.assertNotIn('"new"', path.read_text())
            self.assertNotIn('source_path', path.read_text())

    def test_ledger_rejects_inconsistent_baseline_and_corrupt_state(self):
        cutoff, old, data, state = self.history_fixture()
        data["providers"]["claude"]["daily"][0]["total_tokens"] += 1
        with self.assertRaises(ValueError):
            seed_history(data)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            self.assertIsNone(load_history(path))
            path.write_text('{broken')
            with self.assertRaises(ValueError):
                load_history(path)

    def test_retained_history_still_passes_original_publication_guard(self):
        cutoff, old, previous, state = self.history_fixture()
        later = datetime(2026, 10, 21, 5, tzinfo=timezone.utc)
        stats = aggregate(retain(state, {}, later), later, state["baseline"])
        current = {**previous, "providers": {"codex": stats, "claude": stats}}
        audit = {p: {"files": 1, "unique_model_calls": 1} for p in ("codex", "claude")}
        guard_history(current, previous, audit)
        with self.assertRaises(ValueError):
            guard_history(current, previous, {"codex": audit['codex'], "claude": {}})


    @unittest.skipUnless(zstd_command(), "zstd is required for compressed transcript fixtures")
    def test_compressed_migration_preserves_accounting_and_deduplicates_copies(self):
        now = datetime(2026, 10, 23, 4, tzinfo=timezone.utc)
        first = {"type": "token_usage_record", "timestamp": "2026-10-22T01:00:00Z", "payload": {
            "response_id": "old-response", "usage": {"input_tokens": 100, "output_tokens": 20}}}
        second = {**first, "payload": {"response_id": "new-response", "usage": {
            "input_tokens": 200, "output_tokens": 30}}}
        with tempfile.TemporaryDirectory(prefix="usage fixture 空格 ") as folder:
            root = Path(folder)
            plain = root / "old.jsonl"
            plain.write_text(json.dumps(first) + "\n")
            before, _ = collect([root], "codex", now)
            previous = aggregate(before, now)
            packed = subprocess.run([zstd_command(), "-q", "-c"],
                input=(json.dumps(first) + "\n" + json.dumps(second) + "\n").encode(),
                capture_output=True, check=True).stdout
            (root / "migrated.jsonl.zst").write_bytes(packed)
            records, audit = collect([root, root], "codex", now)
            self.assertEqual(audit["unique_model_calls"], 2)
            self.assertEqual(audit["compressed_files"], 1)
            self.assertEqual(aggregate(records, now)["totals"]["total_tokens"], 350)
            plain.unlink()
            records, _ = collect([root], "codex", now)
            current = aggregate(records, now)
            base = {"schema_version": 2, "generated_at": now.isoformat(), "timezone": "Asia/Singapore"}
            guard_history({**base, "providers": {p: current for p in ("codex", "claude")}},
                          {**base, "providers": {p: previous for p in ("codex", "claude")}},
                          {p: audit for p in ("codex", "claude")})

    @unittest.skipUnless(zstd_command(), "zstd is required for compressed transcript fixtures")
    def test_corrupt_compressed_file_stops_collection(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "broken.jsonl.zst").write_bytes(b"not a zstd stream")
            with self.assertRaisesRegex(ValueError, "compressed usage"):
                collect([root], "codex", datetime.now(timezone.utc))
            # Even a valid first frame must not hide a corrupt trailing frame.
            entry = {"type": "token_usage_record", "timestamp": "2026-10-22T01:00:00Z", "payload": {
                "response_id": "valid-prefix", "usage": {"input_tokens": 100, "output_tokens": 20}}}
            packed = subprocess.run([zstd_command(), "-q", "-c"],
                input=(json.dumps(entry) + '\n').encode(), capture_output=True, check=True).stdout
            (root / "broken.jsonl.zst").write_bytes(packed + b"corrupt trailing frame")
            with self.assertRaisesRegex(ValueError, "compressed usage"):
                collect([root], "codex", datetime.now(timezone.utc))

    def test_missing_decompressor_stops_collection(self):
        with tempfile.TemporaryDirectory() as folder, patch('sync_ai_usage.zstd_command', return_value=None):
            root = Path(folder)
            (root / "unreadable.jsonl.zst").write_bytes(b"placeholder")
            with self.assertRaisesRegex(ValueError, "require zstd"):
                collect([root], "codex", datetime.now(timezone.utc))

    def test_both_provider_ledgers_survive_removed_calls_and_do_not_mix(self):
        cutoff, old, baseline, _ = self.history_fixture()
        baseline['providers']['codex'] = aggregate([{**old, 'input': 400}], cutoff)
        codex_state = seed_history(baseline, "codex")
        claude_state = seed_history(baseline, "claude")
        later = datetime(2026, 10, 21, 5, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            codex_dir = home / '.codex/sessions'
            claude_dir = home / '.claude/projects'
            codex_dir.mkdir(parents=True)
            claude_dir.mkdir(parents=True)
            (codex_dir / 'new.jsonl').write_text(json.dumps({
                'type': 'token_usage_record', 'timestamp': later.isoformat(), 'payload': {
                    'response_id': 'same-private-id', 'usage': {'input_tokens': 200, 'output_tokens': 30}}}) + '\n')
            (claude_dir / 'new.jsonl').write_text(json.dumps({
                'type': 'assistant', 'timestamp': later.isoformat(), 'message': {
                    'id': 'same-private-id', 'usage': {'input_tokens': 300, 'output_tokens': 40}}}) + '\n')
            first, audit = snapshot(home, later, claude_state, codex_state)
            self.assertEqual(first['providers']['codex']['totals']['total_tokens'], 650)
            self.assertEqual(first['providers']['claude']['totals']['total_tokens'], 460)
            for provider, directory in [('codex', codex_dir), ('claude', claude_dir)]:
                (directory / 'new.jsonl').write_text('{}\n')
            second, _ = snapshot(home, later, claude_state, codex_state)
            self.assertEqual(first, second)
            guard_history(second, baseline, audit)
            self.assertNotIn('same-private-id', json.dumps([codex_state, claude_state]))


if __name__ == "__main__":
    unittest.main()
