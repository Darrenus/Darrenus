# AI workload statistics

This section measures local model activity, not plan quotas or API spending.
It covers Codex and Claude Code request counters available on this device.
ChatGPT and Claude web conversations, other devices, and usage before local
records began are not included. Deleted logs are covered only if their numeric
counters were retained before deletion. Subscription billing and payment
method are not present in these counters, so the cards use product names rather
than claiming every request came from a particular paid plan.

## Metrics

- **Total tokens processed:** input + output, including cached input. This
  counts repeated context processing, not unique words written by the user.
- **Average tokens / week:** tokens in the last 28 Singapore calendar dates,
  including the current partial day, divided by four. Inactive dates count.
- **Active days:** dates with at least one recorded model call in that window.
- **Model calls:** unique API response/message IDs, including tool loops and
  subagent calls. This is not the number of human messages.
- **Calls / active day:** model calls divided by active dates in the same window.
- **Ring:** each provider's share of tokens processed in the same 28-day window.
  The centre is the combined 28-day token total. Cumulative histories can start
  on different dates, so cumulative totals are not used for the ring's shares.
- **Daily heatmaps:** one square per day, reading left to right across the first
  row and then the second. Both providers use the same square-root colour
  intensity scale; zero-activity dates are neutral. SVG cell titles contain exact
  dates and token counts. Compact visual labels are rounded.

The module has light/dark desktop layouts and a stacked mobile layout.

Input/output totals, all daily counters and exact cumulative counts are saved
in `data/ai-usage.json`. Cards display the observation time; they are snapshots.

## Accounting and deduplication

Codex uses per-response `token_usage_record.payload.usage`, deduplicated by
`response_id`. Its input already includes cache reads. Do not add cumulative
thread/turn token counters or duplicate `event_msg` notifications. Reasoning
tokens are already included in output.

Claude Code uses assistant messages with usage, deduplicated globally by
`message.id`, including across copied transcripts and subagent files. Streaming
updates merge the maximum of each counter for that message; they are not summed.
Total input = `input_tokens` + `cache_read_input_tokens` +
`cache_creation_input_tokens`. Output includes thinking, so thinking counters,
cache TTL breakdowns, and nested `iterations` are not added a second time.

### Retaining historical counts

Claude Code can remove old transcripts during its retention sweep. The local
publisher therefore keeps a private numeric ledger outside the Git checkout,
in a sibling `.ai-usage-state` directory. Only timestamps, numeric counters,
and SHA-256 message-ID digests are retained; no conversation content or original
IDs are stored there. The ledger and its previous valid backup are written
atomically with owner-only file access and are never staged or pushed.

The initial historical baseline is the last complete published snapshot from
[20 October 2026](https://github.com/Darrenus/Darrenus/commit/6c4c261e2ff40ff826130d2ee2bf488cd7ea2c2a).
Its daily counters are checked against its totals before import. Calls with
timestamps at or before that snapshot's observation time belong exclusively to
the sealed baseline; later calls belong exclusively to the private ledger.
Restoring an old transcript therefore does not add its calls twice. Streaming
updates for later calls retain the maximum counters rather than summing copies.
Updates to calls already sealed in the baseline are not retroactively estimated.

The publisher still applies the existing cumulative and per-day history-loss
checks. It does not fill missing counts with guesses or take a maximum between
independently recomputed daily totals. A missing/corrupt ledger or unavailable
local logs stops publication when validation fails. Raw deleted conversations
cannot be recreated from these aggregate counters. Calls deleted before their
first successful collection cannot be recovered from the ledger.

The parser fails on invalid usage counters rather than publishing an incorrect
total. Records without usable request-level token counters are not estimated.

## Refresh

```sh
python3 scripts/sync_ai_usage.py
python3 -m unittest discover -s scripts -p 'test_*.py'
git diff -- data/ai-usage.json assets/ai-*.svg
git add data/ai-usage.json assets/ai-*.svg
git commit -m "chore: refresh AI workload statistics"
git push
```

This reads numeric counters from `~/.codex/sessions`,
`~/.codex/archived_sessions`, and `~/.claude/projects`. It makes no model or
provider API calls. Only aggregate dates and counts are exported: no prompts,
responses, IDs, project names, local paths, account details or credentials.

## Daily automatic publication

The daily chat automation runs `python3 scripts/publish_ai_usage.py` at 12:00
Asia/Singapore in this local checkout. Keep the computer awake, the Codex desktop
app running, and GitHub authentication valid at the scheduled time.

The helper pulls current changes, aggregates local and retained counters, validates retained
history, runs the accounting tests, and commits/pushes only the data JSON and
four workload SVGs. It stops if local work would be disturbed or logs are missing.
Concurrent GitHub statistics updates are rebased only for its own generated-file
commits. A failed push can be resumed on the next run; it never force-pushes.
Routine success is quiet; failures requiring attention are reported in this chat.

GitHub Actions re-renders the saved workload data and updates GitHub statistics.
It cannot read files on the user's computer. The local automation supplies new
activity; re-rendering alone never advances the observation time or pretends that
a saved snapshot is a live account-wide meter.
