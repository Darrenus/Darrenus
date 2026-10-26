"""Refresh local aggregate workload and publish only the five generated files.

The daily chat automation runs this helper. It never stages raw local logs or
unrelated work. Missing history, validation errors and offline failures stop the
run. A failed push can be resumed safely on the next run.
"""

import fcntl
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sync_ai_usage import ROOT, snapshot, write_snapshot
from usage_cards import METRICS, validate
from usage_history import HISTORY_PATH, CODEX_HISTORY_PATH, load_history, save_history

MESSAGE = "chore: refresh AI workload statistics [auto]"
FILES = (
    "data/ai-usage.json",
    "assets/ai-workload-light.svg", "assets/ai-workload-dark.svg",
    "assets/ai-workload-mobile-light.svg", "assets/ai-workload-mobile-dark.svg",
)


def git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], text=True,
                          capture_output=True, check=True, timeout=90).stdout.strip()


def guard_history(current, previous, audit):
    validate(current)
    for provider, old in previous["providers"].items():
        if not audit[provider].get("files") or not audit[provider].get("unique_model_calls"):
            raise ValueError(f"{provider} local usage records are unavailable; keeping previous snapshot")
        stats = current["providers"][provider]
        for key in METRICS:
            if stats["totals"][key] < old["totals"][key]:
                raise ValueError(f"{provider} cumulative history decreased; keeping previous snapshot")
        by_day = {day["date"]: day for day in stats["daily"]}
        for day in old["daily"]:
            if any(by_day.get(day["date"], {}).get(key, 0) < day[key] for key in METRICS):
                raise ValueError(f"{provider} historical daily records are missing; keeping previous snapshot")


def pending_commits():
    return git("rev-list", "origin/main..HEAD").splitlines()


def check_pending(commits):
    for commit in commits:
        if git("show", "-s", "--format=%s", commit) != MESSAGE:
            raise ValueError("Unpublished user commits exist; refusing to push unrelated work")
        paths = set(git("diff-tree", "--no-commit-id", "--name-only", "-r", commit).splitlines())
        if not paths or not paths.issubset(FILES):
            raise ValueError("Pending commit contains files outside the generated workload data")


def rebase_pending():
    try:
        git("rebase", "origin/main")
    except subprocess.CalledProcessError:
        git("rebase", "--abort")
        raise ValueError("Concurrent remote edits conflict with the snapshot; no changes were overwritten") from None


def publish():
    origin = git("remote", "get-url", "origin")
    if origin not in {"https://github.com/Darrenus/Darrenus.git", "git@github.com:Darrenus/Darrenus.git"}:
        raise ValueError("Unexpected repository origin")
    if git("branch", "--show-current") != "main" or git("status", "--porcelain"):
        raise ValueError("The main checkout contains local edits; leaving them untouched")
    git("fetch", "origin", "main")
    commits = pending_commits()
    check_pending(commits)
    if commits:
        rebase_pending()
    else:
        git("merge", "--ff-only", "origin/main")
    previous = json.loads((ROOT / "data/ai-usage.json").read_text())
    state = load_history(HISTORY_PATH)
    codex_state = load_history(CODEX_HISTORY_PATH)
    current, audit = snapshot(Path.home(), datetime.now(timezone.utc), state, codex_state)
    guard_history(current, previous, audit)
    subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "scripts", "-p", "test_*.py"],
                   cwd=ROOT, check=True, capture_output=True, text=True, timeout=30)
    if state is not None:
        save_history(HISTORY_PATH, state)
    if codex_state is not None:
        save_history(CODEX_HISTORY_PATH, codex_state)
    write_snapshot(current)
    git("add", "--", *FILES)
    staged = set(git("diff", "--cached", "--name-only").splitlines())
    if not staged.issubset(FILES):
        raise ValueError("Other staged work appeared during synchronization; refusing to commit it")
    if staged:
        git("commit", "-m", MESSAGE)
    try:
        git("push", "origin", "HEAD:main")
    except subprocess.CalledProcessError:
        # The daily GitHub stats job may have pushed while aggregation ran.
        git("fetch", "origin", "main")
        check_pending(pending_commits())
        rebase_pending()
        git("push", "origin", "HEAD:main")
    print("Published verified AI workload aggregates to Darrenus/Darrenus.")


if __name__ == "__main__":
    try:
        with (Path(tempfile.gettempdir()) / "darrenus-profile-ai-usage.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            publish()
    except BlockingIOError:
        print("Another workload sync is already running; skipped.")
    except (ValueError, subprocess.SubprocessError, OSError) as error:
        # Avoid dumping command output or any local transcript on failures.
        print(str(error) if isinstance(error, ValueError) else "Automatic publication failed; check connectivity and GitHub authentication.", file=sys.stderr)
        sys.exit(1)
