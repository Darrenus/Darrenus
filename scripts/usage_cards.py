"""Render public aggregate AI activity. This module never reads local chat logs."""

import json
from datetime import date, datetime, timedelta
from html import escape
from math import pi, sqrt
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


PROVIDERS = (("codex", "Codex"), ("claude", "Claude Code"))


def daily_values(stats):
    start = date.fromisoformat(stats["last_28_days"]["start_date"])
    by_day = {day["date"]: day["total_tokens"] for day in stats["daily"]}
    return [(start + timedelta(days=i), by_day.get((start + timedelta(days=i)).isoformat(), 0)) for i in range(28)]


def blend(track, accent, amount):
    a, b = (tuple(int(color[i:i+2], 16) for i in (1, 3, 5)) for color in (track, accent))
    return "#" + "".join(f"{round(x+(y-x)*amount):02x}" for x, y in zip(a, b))


def donut(x, y, radius, values, accents, track):
    total = sum(values)
    circumference = 2*pi*radius
    width = 14
    body = f'<circle cx="{x}" cy="{y}" r="{radius}" fill="none" stroke="{track}" stroke-width="{width}"/>'
    offset = 0
    for value, accent in zip(values, accents):
        if value and total:
            length = circumference*value/total
            # The tiny neutral gap separates categories without outlining marks.
            gap = 3 if all(values) else 0
            body += f'<circle cx="{x}" cy="{y}" r="{radius}" fill="none" stroke="{accent}" stroke-width="{width}" stroke-dasharray="{max(0,length-gap):.3f} {circumference:.3f}" stroke-dashoffset="{-offset:.3f}" transform="rotate(-90 {x} {y})"/>'
            offset += length
    body += text(x, y-2, short(total), "ring-value", text_anchor="middle")
    body += text(x, y+20, "tokens / 28 days", "muted", text_anchor="middle")
    return body


def heatmap(x, y, values, maximum, accent, track):
    body = ""
    # Both providers share one square-root intensity scale, not separate maxima.
    for i, (day, value) in enumerate(values):
        color = blend(track, accent, .18+.82*sqrt(value/maximum)) if value and maximum else track
        title = escape(f"{day.isoformat()}: {value:,} tokens")
        body += f'<rect x="{x+(i%14)*23}" y="{y+(i//14)*23}" width="19" height="19" rx="3" fill="{color}"><title>{title}</title></rect>'
    body += text(x, y+61, values[0][0].strftime("%b %d"), "muted")
    body += text(x+318, y+61, values[-1][0].strftime("%b %d"), "muted", text_anchor="end")
    return body


def provider_panel(x, y, stats, name, accent, track, maximum):
    totals, recent = stats["totals"], stats["last_28_days"]
    body = f'<circle cx="{x+4}" cy="{y-5}" r="4" fill="{accent}"/>'
    body += text(x+17, y, name, "provider")
    body += text(x, y+40, short(totals["total_tokens"]) if stats["first_recorded_date"] else "No records", "hero")
    body += text(x, y+61, f"Cumulative since {stats['first_recorded_date']}" if stats["first_recorded_date"] else "No local request records", "muted")
    body += heatmap(x, y+80, daily_values(stats), maximum, accent, track)
    rows = [
        ("Avg. tokens / week", short(recent["average_tokens_per_week"])),
        ("Active days", f"{recent['active_days']} / 28"),
        ("Model calls", f"{recent['model_calls']:,}"),
        ("Calls / active day", f"{recent['model_calls_per_active_day']:g}"),
    ]
    for i, (label, value) in enumerate(rows):
        body += text(x, y+171+i*25, label)
        body += text(x+318, y+171+i*25, value, "number", text_anchor="end")
    return body


def render_usage(data=None):
    data = validate(data if data is not None else json.loads((ROOT / "data/ai-usage.json").read_text()))
    updated = datetime.fromisoformat(data["generated_at"]).astimezone(ZoneInfo("Asia/Singapore")).strftime("%d %b %Y, %H:%M SGT")
    providers = data["providers"]
    periods = {(s["last_28_days"]["start_date"], s["last_28_days"]["end_date"]) for s in providers.values()}
    if len(periods) != 1:
        raise ValueError("Provider shares require an identical observation window")
    start, end = next(iter(periods))
    values = [providers[key]["last_28_days"]["total_tokens"] for key, _ in PROVIDERS]
    total = sum(values)
    maximum = max(value for s in providers.values() for _, value in daily_values(s))
    for theme in ("light", "dark"):
        accents = ("#39c99b", "#e99a79") if theme == "dark" else ("#09886b", "#bc603f")
        track = "#212830" if theme == "dark" else "#eaeef2"
        for mobile in (False, True):
            width, height = (360, 1000) if mobile else (720, 558)
            body = text(22, 32, "Token workload", "provider")
            body += text(width-22, 55 if mobile else 32, f"{start[5:]} — {end[5:]} · 28 days", "muted", text_anchor="end")
            cx, cy = (180, 151) if mobile else (110, 119)
            body += donut(cx, cy, 69, values, accents, track)
            legend_x, legend_y = (22, 261) if mobile else (224, 84)
            for i, ((key, name), value, accent) in enumerate(zip(PROVIDERS, values, accents)):
                y = legend_y+i*35
                body += f'<circle cx="{legend_x+4}" cy="{y-5}" r="4" fill="{accent}"/>'
                body += text(legend_x+17, y, name)
                share = f"{100*value/total:.1f}%" if total else "—"
                body += text(width-22, y, f"{share} · {short(value)}", "number", text_anchor="end")
            weekly_y = legend_y+82
            body += text(legend_x, weekly_y, "Combined weekly average", "muted")
            body += text(width-22, weekly_y+23 if mobile else weekly_y, short(total/4), "number", text_anchor="end")
            divider_y = 390 if mobile else 217
            body += f'<path d="M22 {divider_y}H{width-22}" class="rule"/>'
            for i, ((key, name), accent) in enumerate(zip(PROVIDERS, accents)):
                x, y = (22, 420+i*266) if mobile else (22+i*356, 250)
                body += provider_panel(x, y, providers[key], name, accent, track, maximum)
            footer_y = height-37
            body += text(22, footer_y, "Daily token heatmap · common intensity scale", "muted")
            body += text(22, footer_y+20, f"Snapshot · {updated}", "muted")
            output = svg(body, height, theme, "AI workload: 28-day token share and daily activity; includes cached input", width=width)
            css = '.hero{font-size:32px;font-weight:650;letter-spacing:-.5px}.ring-value{font-size:28px;font-weight:600}.provider{font-size:17px;font-weight:600}.rule{fill:none;stroke:' + ("#30363d" if theme == "dark" else "#d1d9e0") + ';stroke-width:1}'
            output = output.replace("</style>", css + '</style>')
            suffix = "-mobile" if mobile else ""
            (ROOT / "assets" / f"ai-workload{suffix}-{theme}.svg").write_text(output, encoding="utf-8")


if __name__ == "__main__":
    render_usage()
