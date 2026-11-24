"""Merge Diomedes’s daily history with the snapshot tracked by Pillars."""

import datetime
import json
import os
import subprocess
import tempfile
from pathlib import Path


PROFILE = "diomedes"
USER_FILE = Path("users") / f"{PROFILE}.json"
REMOTE_FILE = (Path("data") / USER_FILE).as_posix()
REMOTE_REF = "refs/heads/main"
ACTIVITY_FIELDS = {"anki": "reviews", "speak": "attempts"}


class HistoryError(Exception):
    """History could not be read or synchronized."""


def empty_history():
    return {
        "version": 1,
        "user": PROFILE,
        "reading": {"daily_goal_minutes": 0, "days": {}},
        "days": {},
    }


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as exc:
        raise HistoryError(f"Could not read {path.name}: {exc}") from exc


def write_json(path, value):
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False,
        ) as output:
            temporary = Path(output.name)
            json.dump(value, output, ensure_ascii=False, sort_keys=True, indent=2)
            output.write("\n")
        temporary.replace(path)
    except (OSError, ValueError) as exc:
        raise HistoryError(f"Could not save {path.name}: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def validate_history(value):
    if not isinstance(value, dict) or value.get("version") != 1 or value.get("user") != PROFILE:
        raise HistoryError("Expected a version 1 history snapshot for diomedes.")
    reading = value.get("reading")
    days = value.get("days")
    if not isinstance(reading, dict) or not isinstance(reading.get("days"), dict) or not isinstance(days, dict):
        raise HistoryError("The history snapshot is missing daily records.")

    for entry in reading["days"].values():
        if not isinstance(entry, dict) or not isinstance(entry.get("minutes"), int):
            raise HistoryError("The history snapshot contains an invalid reading record.")
    for activities in days.values():
        if not isinstance(activities, dict):
            raise HistoryError("The history snapshot contains an invalid day.")
        for pillar, item in activities.items():
            if (
                pillar not in ("anki", "major", "speak")
                or not isinstance(item, dict)
                or item.get("id") != pillar
                or not isinstance(item.get("done"), bool)
                or not isinstance(item.get("title"), str)
                or not isinstance(item.get("detail"), str)
                or not isinstance(item.get("auto"), bool)
                or not isinstance(item.get(ACTIVITY_FIELDS.get(pillar), 0), int)
                or not isinstance(item.get("duration_sec", 0), (int, float))
            ):
                raise HistoryError("The history snapshot contains an invalid pillar record.")
    return value


def load_history(data_dir):
    if not history_is_configured(data_dir):
        return empty_history()
    cached = validate_history(read_json(data_dir / "history.json", empty_history()))
    tracked = validate_history(read_json(data_dir / USER_FILE, empty_history()))
    return merge_history(cached, tracked)


def history_is_configured(data_dir):
    config = read_json(data_dir / "config.json", None)
    return isinstance(config, dict) and config.get("user") == PROFILE


def reading_timestamp(entry):
    value = entry.get("updated_at")
    try:
        # Legacy reading entries use machine-local timestamps with no offset.
        return datetime.datetime.fromisoformat(value).astimezone(datetime.timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)


def merge_reading(local, other):
    result = {
        "daily_goal_minutes": local.get("daily_goal_minutes") or other.get("daily_goal_minutes", 0),
        "days": {},
    }
    for readings in (local, other):
        for date, entry in readings.get("days", {}).items():
            if not isinstance(entry, dict):
                continue
            timestamp = reading_timestamp(entry)
            current = result["days"].get(date)
            if current is None or timestamp > reading_timestamp(current):
                result["days"][date] = {**entry, "updated_at": timestamp.isoformat()}
    return result


def prefer_activity(local_item, other_item):
    if not local_item or not other_item:
        return local_item or other_item
    field = ACTIVITY_FIELDS.get(local_item["id"])
    local_rank = (local_item.get(field, 0), local_item["done"], local_item.get("duration_sec", 0))
    other_rank = (other_item.get(field, 0), other_item["done"], other_item.get("duration_sec", 0))
    return other_item if other_rank > local_rank else local_item


def merge_history(local, other):
    merged = empty_history()
    merged["reading"] = merge_reading(local["reading"], other["reading"])
    merged["days"] = {date: activities.copy() for date, activities in local["days"].items()}
    for date, activities in other["days"].items():
        day = merged["days"].setdefault(date, {})
        for pillar, item in activities.items():
            day[pillar] = prefer_activity(day.get(pillar), item)
    return merged


def set_history_user(data_dir, user):
    if user != PROFILE:
        raise HistoryError(f"History sync supports the {PROFILE} profile.")
    write_json(data_dir / "config.json", {"user": user})


def git(repo, *args, allow_failure=False):
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        env.pop(key, None)
    try:
        result = subprocess.run(
            ["git", *args], cwd=repo, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
    except OSError as exc:
        raise HistoryError(f"Could not run Git: {exc}") from exc
    if result.returncode and not allow_failure:
        raise HistoryError(result.stderr.strip() or f"Git {args[0]} failed.")
    return result


def fetch_snapshot(repo, remote, push_remote):
    git(repo, "init", "--quiet", "--initial-branch=main")
    git(repo, "remote", "add", "--", "origin", remote)
    if push_remote != remote:
        git(repo, "remote", "set-url", "--push", "origin", push_remote)
    branch = git(repo, "ls-remote", "--exit-code", "origin", REMOTE_REF, allow_failure=True)
    if branch.returncode == 2:
        return None
    if branch.returncode:
        raise HistoryError(branch.stderr.strip() or "Could not contact the history repository.")

    git(repo, "fetch", "--quiet", "--no-tags", "origin", REMOTE_REF)
    git(repo, "reset", "--quiet", "--mixed", "FETCH_HEAD")
    files = git(repo, "ls-tree", "--name-only", "HEAD", "--", REMOTE_FILE).stdout
    if not files.strip():
        return None
    try:
        return validate_history(json.loads(git(repo, "show", f"HEAD:{REMOTE_FILE}").stdout))
    except ValueError as exc:
        raise HistoryError("The remote history snapshot contains invalid JSON.") from exc


def sync_history(data_dir, action, local_history=None):
    if not history_is_configured(data_dir):
        raise HistoryError("Run `pillars user set diomedes` first.")
    root = data_dir.parent
    remote = origin_url(root)
    push_remote = origin_url(root, "--push") if action == "push" else remote
    cached = load_history(data_dir)
    if local_history is not None:
        validate_history(local_history)

    for _ in range(3):
        with tempfile.TemporaryDirectory(prefix="pillars-history-") as temporary:
            repo = Path(temporary)
            upstream = fetch_snapshot(repo, remote, push_remote)
            merged = merge_history(upstream or empty_history(), cached)
            if local_history is not None:
                merged = merge_history(local_history, merged)
            if action == "fetch":
                if upstream is None:
                    raise HistoryError("No history has been pushed yet. Run `pillars sync push` on the source install first.")
                break
            if merged == upstream:
                break

            write_json(repo / REMOTE_FILE, merged)
            git(repo, "add", "--", REMOTE_FILE)
            changes = git(repo, "diff", "--cached", "--name-only", "--", REMOTE_FILE).stdout
            if not changes.strip():
                break
            git(repo, "commit", "--quiet", "-m", f"Update {PROFILE} history")
            pushed = git(repo, "push", "--porcelain", "origin", f"HEAD:{REMOTE_REF}", allow_failure=True)
            if not pushed.returncode:
                break
            retry_reasons = ("(fetch first)", "(non-fast-forward)", "(failed to update ref)")
            if (
                not any(reason in pushed.stdout for reason in retry_reasons)
                and f"cannot lock ref '{REMOTE_REF}'" not in pushed.stderr
            ):
                raise HistoryError(pushed.stderr.strip() or pushed.stdout.strip() or "Could not push the history.")
    else:
        raise HistoryError("History changed on the remote during three push attempts. Run `pillars sync push` again.")

    merged = merge_history(merged, load_history(data_dir))
    write_json(data_dir / "history.json", merged)
    return merged


def origin_url(repo, *options):
    url = git(repo, "remote", "get-url", *options, "origin").stdout.strip()
    if ":" not in url:
        url = str((repo / url).resolve())
    return url
