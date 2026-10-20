# AI workload statistics

This section measures local model activity, not plan quotas or API spending.
It covers Codex and Claude Code request counters available on this device.
ChatGPT and Claude web conversations, other devices, deleted logs, and usage
before local records began are not included. Subscription billing and payment
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
- **Daily activity bars:** last 28 dates on a log(1 + tokens) scale. Card totals
  and daily counters are exact; compact visual labels are rounded.

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

GitHub Actions re-renders the saved workload data and updates GitHub statistics.
It cannot read files on the user's computer. New local activity requires running
the local sync command again; re-rendering never advances the observation time
or pretends that a saved snapshot is a live account-wide meter.
