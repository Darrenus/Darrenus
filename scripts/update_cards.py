"""Render profile cards using public GitHub data. Requires Python 3.9+ and gh.

Uses the workflow's GITHUB_TOKEN; no personal access token is required. Public
repository totals are paginated. Contribution counts cover GitHub's default
12-month window, up to its maximum of 100 repositories per contribution type.
Private repository contributions are explicitly excluded, even on local runs.
"""

import json
import subprocess
from html import escape
from pathlib import Path

OWNER = "Darrenus"
ROOT = Path(__file__).resolve().parents[1]
KINDS = ("commit", "pullRequest", "issue", "pullRequestReview")


def gh(*args, payload=None):
    result = subprocess.run(
        ["gh", "api", *args],
        input=json.dumps(payload) if payload else None,
        text=True, capture_output=True, check=True,
    )
    return json.loads(result.stdout)


def load_data():
    pages = gh(f"users/{OWNER}/repos?per_page=100&type=owner", "--paginate", "--slurp")
    repos = [repo for page in pages for repo in page if not repo["private"]]
    fields = " ".join(
        f"{kind}ContributionsByRepository(maxRepositories: 100) "
        "{ repository { isPrivate } contributions { totalCount } }"
        for kind in KINDS
    )
    response = gh("graphql", "--input", "-", payload={
        "query": "query($login: String!) { user(login: $login) { "
        "contributionsCollection { startedAt endedAt " + fields + " } } }",
        "variables": {"login": OWNER},
    })
    if response.get("errors"):
        raise RuntimeError(response["errors"])
    collection = response["data"]["user"]["contributionsCollection"]
    counts = {
        kind: sum(entry["contributions"]["totalCount"]
                  for entry in collection[f"{kind}ContributionsByRepository"]
                  if not entry["repository"]["isPrivate"])
        for kind in KINDS
    }
    return repos, collection, counts


def text(x, y, value, cls="body", **attrs):
    attributes = " ".join(f'{key.replace("_", "-")}="{escape(str(value), quote=True)}"'
                          for key, value in attrs.items())
    return f'<text x="{x}" y="{y}" class="{cls}" {attributes}>{escape(str(value))}</text>'


def svg(body, height, theme, title):
    bg, border, fg, muted, accent = (
        ("#0d1117", "#30363d", "#e6edf3", "#9198a1", "#79c0ff")
        if theme == "dark" else
        ("#ffffff", "#d1d9e0", "#1f2328", "#59636e", "#0969da")
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="420" height="{height}" viewBox="0 0 420 {height}" role="img" aria-labelledby="title">
<title id="title">{escape(title)}</title>
<style>text{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;fill:{fg}}}.body{{font-size:14px}}.title{{font-size:18px;font-weight:600;fill:{accent}}}.muted{{font-size:12px;fill:{muted}}}.number{{font-size:15px;font-weight:600}}.accent{{fill:{accent}}}</style>
<rect x=".5" y=".5" width="419" height="{height-1}" rx="8" fill="{bg}" stroke="{border}"/>
{body}
</svg>
'''


def render(repos, collection, counts):
    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)
    stars = sum(repo["stargazers_count"] for repo in repos if not repo["fork"])
    rows = [("Public repositories", len(repos)), ("Stars earned", stars),
            ("Commits", counts["commit"]), ("Pull requests", counts["pullRequest"]),
            ("Issues", counts["issue"]), ("Code reviews", counts["pullRequestReview"])]
    end = collection["endedAt"][:10]
    cards = [
        ("coding-agent", "coding-agent-from-scratch", [
            "A terminal coding agent, built from scratch.",
            "ReAct · repository maps · sandboxed tools"], "Python", "#3572A5"),
        ("bodycraft", "bodycraft-oss", [
            "Multi-path retrieval over technical documents.",
            "Full-text · vectors · graphs · ranking evaluation"], "Python", "#3572A5"),
    ]
    for theme in ("light", "dark"):
        body = text(22, 34, "GitHub Stats", "title")
        body += text(22, 56, "Public activity · past 12 months", "muted")
        for i, (label, count) in enumerate(rows):
            y = 89 + 27 * i
            body += text(22, y, label)
            body += text(396, y, f"{count:,}", "number", text_anchor="end")
        body += text(22, 259, f"Updated {end} · repository & star totals are all-time", "muted")
        (assets / f"github-stats-{theme}.svg").write_text(
            svg(body, 278, theme, "Rong's GitHub statistics"), encoding="utf-8")
        for slug, title, lines, lang, color in cards:
            body = text(18, 29, title, "title")
            for i, line in enumerate(lines):
                body += text(18, 54 + 20 * i, line, "muted")
            body += f'<circle cx="23" cy="98" r="5" fill="{color}"/>'
            body += text(35, 102, lang, "muted")
            (assets / f"{slug}-{theme}.svg").write_text(
                svg(body, 120, theme, title + ": " + lines[0]), encoding="utf-8")


if __name__ == "__main__":
    render(*load_data())
    print("Updated six profile cards from public GitHub data.")
