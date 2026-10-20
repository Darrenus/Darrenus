"""Render public aggregate AI activity. This module never reads local chat logs."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from update_cards import ROOT, svg, text

METRICS = {"total_tokens", "input_tokens", "output_tokens", "cache_read_tokens", "model_calls"}


def validate(data):
    if set(data) != {"schema_version", "generated_at", "timezone", "providers"} or data["schema_version"] != 2:
        raise ValueError("Invalid public workload schema")
    datetime.fromisoformat(data["generated_at"])
    if data["timezone"] != "Asia/Singapore" or set(data["providers"]) != {"codex", "claude"}:
        raise ValueError("Invalid workload providers or timezone")
    for stats in data["providers"].values():
        if set(stats) != {"first_recorded_date", "last_recorded_date", "totals", "last_28_days", "daily"}:
            raise ValueError("Unexpected fields in public workload data")
        if set(stats["totals"]) != METRICS:
            raise ValueError("Only aggregate token and call counts may be published")
        for value in stats["totals"].values():
            if type(value) is not int or value < 0:
                raise ValueError("Invalid aggregate counter")
        recent = stats["last_28_days"]
        if set(recent) != METRICS | {"start_date", "end_date", "active_days", "average_tokens_per_week", "model_calls_per_active_day"}:
            raise ValueError("Unexpected recent activity fields")
        if not 0 <= recent["active_days"] <= 28:
            raise ValueError("Invalid active day count")
        for day in stats["daily"]:
            if set(day) != METRICS | {"date"}:
                raise ValueError("Unexpected daily data fields")
        totals = stats["totals"]
        if totals["total_tokens"] != totals["input_tokens"] + totals["output_tokens"]:
            raise ValueError("Aggregate input and output do not match total")
    return data


def short(value):
    for unit, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1000, "K")):
        if value >= unit:
            return f"{value/unit:.2f}".rstrip("0").rstrip(".") + suffix
    return f"{value:,.0f}"


def render_usage(data=None):
    data = validate(data if data is not None else json.loads((ROOT / "data/ai-usage.json").read_text()))
    updated = datetime.fromisoformat(data["generated_at"]).astimezone(ZoneInfo("Asia/Singapore")).strftime("%d %b %Y, %H:%M SGT")
    for provider, name, accent in (("codex", "Codex", "#10a37f"), ("claude", "Claude Code", "#d97757")):
        stats = data["providers"][provider]
        totals, recent = stats["totals"], stats["last_28_days"]
        total = totals["total_tokens"]
        for theme in ("light", "dark"):
            body = f'<circle cx="27" cy="28" r="5" fill="{accent}"/>'
            body += text(40, 34, name, "title")
            body += text(22, 59, "TOTAL TOKENS PROCESSED", "muted")
            body += text(22, 100, short(total) if stats["first_recorded_date"] else "No records", "hero")
            body += text(22, 123, f"Since {stats['first_recorded_date']} · includes cached input" if stats["first_recorded_date"] else "No usage data found on this device", "muted")
            rows = [
                ("Avg. tokens / week · last 28d", short(recent["average_tokens_per_week"])),
                ("Active days · last 28d", f"{recent['active_days']} / 28"),
                ("Model calls · last 28d", f"{recent['model_calls']:,}"),
                ("Calls / active day · last 28d", f"{recent['model_calls_per_active_day']:g}"),
            ]
            for i, (label, value) in enumerate(rows):
                body += text(22, 160+i*27, label)
                body += text(398, 160+i*27, value, "number", text_anchor="end")
            # Log-scale daily bars keep high-traffic days from obscuring activity.
            from datetime import date, timedelta
            from math import log1p
            start = date.fromisoformat(recent["start_date"])
            by_day = {day["date"]: day["total_tokens"] for day in stats["daily"]}
            values = [by_day.get((start+timedelta(days=i)).isoformat(), 0) for i in range(28)]
            top = max(values, default=0)
            track = "#212830" if theme == "dark" else "#eaeef2"
            for i, value in enumerate(values):
                height = 2 + 26 * log1p(value)/log1p(top) if top else 2
                body += f'<rect x="{22+i*13.5:.1f}" y="{281-height:.1f}" width="9" height="{height:.1f}" rx="2" fill="{accent if value else track}"/>'
            body += text(22, 300, "Daily token activity · last 28d · log scale", "muted")
            body += text(22, 323, f"Input {short(totals['input_tokens'])} · Output {short(totals['output_tokens'])}", "muted")
            body += text(22, 345, f"Snapshot · {updated}", "muted")
            output = svg(body, 365, theme, f"{name}: {total:,} tokens processed from local logs")
            output = output.replace("</style>", '.hero{font-size:38px;font-weight:650;letter-spacing:-1px}</style>')
            (ROOT / "assets" / f"ai-{provider}-{theme}.svg").write_text(output, encoding="utf-8")


if __name__ == "__main__":
    render_usage()
