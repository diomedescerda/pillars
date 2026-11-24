#!/usr/bin/env python3
"""
pillars — 4-Pillar Daily Accountability Engine (Anki + Major/grow + Speaking/speak + Read in English)
Pulls live Proof of Work from Anki, grow, and speak, and tracks daily English reading time.
"""

import argparse
from contextlib import closing
import datetime
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from pillars_history import (
    HistoryError,
    history_is_configured,
    load_history,
    merge_reading,
    prefer_activity,
    set_history_user,
    sync_history,
    write_json,
)
from pillars_schedule import disable_schedule, enable_schedule

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
READING_FILE = DATA_DIR / "reading.json"

ANKI_DB = Path.home() / ".local" / "share" / "Anki2" / "ユーザー 1" / "collection.anki2"
GROW_DIR = Path.home() / "personal" / "growing"
GROW_CONFIG = GROW_DIR / "data" / "config.json"
GROW_USERS_DIR = GROW_DIR / "data" / "users"
SPEAK_DIOMEDES_DB = Path.home() / "work" / "Speak" / "data" / "diomedes" / "speak.db"
SPEAK_PERSONAL_DIOMEDES_DB = Path.home() / "personal" / "speak" / "data" / "diomedes" / "speak.db"
SPEAK_LOCAL_DB = Path.home() / ".local" / "share" / "speak" / "speak.db"

# TrueColor ANSI styling (zero emojis)
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

FG_WHITE = "\033[38;2;248;250;252m"
FG_TEXT = "\033[38;2;203;213;225m"
FG_MUTED = "\033[38;2;100;116;139m"
FG_BORDER = "\033[38;2;51;65;85m"
FG_GREEN = "\033[38;2;52;211;153m"
FG_CYAN = "\033[38;2;56;189;248m"
FG_AMBER = "\033[38;2;251;191;36m"
FG_RED = "\033[38;2;248;113;113m"

BG_GREEN_BADGE = "\033[48;2;16;185;129m\033[38;2;15;23;42m"
BG_CYAN_BADGE = "\033[48;2;14;165;233m\033[38;2;15;23;42m"
BG_AMBER_BADGE = "\033[48;2;245;158;11m\033[38;2;15;23;42m"
BG_DARK_BADGE = "\033[48;2;30;41;59m\033[38;2;148;163;184m"

# Legacy aliases used in CLI messages
CYAN = FG_CYAN
GREEN = FG_GREEN
YELLOW = FG_AMBER
RED = FG_RED
BLUE = FG_CYAN


def today_date() -> datetime.date:
    return datetime.date.today()


def today_str() -> str:
    return today_date().isoformat()


def load_local_reading_data() -> Dict[str, Any]:
    if READING_FILE.exists():
        try:
            data = json.loads(READING_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("daily_goal_minutes", 0)
                data.setdefault("days", {})
                return data
        except Exception:
            pass
    return {"daily_goal_minutes": 0, "days": {}}


def load_reading_data() -> Dict[str, Any]:
    return merge_reading(load_local_reading_data(), load_history(DATA_DIR)["reading"])


def save_reading_data(data: Dict[str, Any]) -> None:
    write_json(READING_FILE, data)


# ---------------------------------------------------------------------------
# 1. ANKI PROOF OF WORK
# ---------------------------------------------------------------------------
def get_anki_day_window_ms(d: datetime.date) -> Tuple[int, int]:
    """Anki's day rollover defaults to 04:00 AM local time."""
    start_dt = datetime.datetime.combine(d, datetime.time(4, 0))
    end_dt = start_dt + datetime.timedelta(days=1)
    return int(start_dt.timestamp() * 1000), int(end_dt.timestamp() * 1000)


def check_anki(d: Optional[datetime.date] = None) -> Dict[str, Any]:
    target_date = d or today_date()
    is_today = target_date == today_date()

    if not ANKI_DB.exists():
        return {
            "id": "anki",
            "title": "Anki Reviews",
            "done": False,
            "auto": True,
            "detail": "Anki database not found",
            "reviews": 0,
            "minutes": 0.0,
            "due_remaining": 0,
        }

    try:
        conn = sqlite3.connect(f"file:{ANKI_DB}?immutable=1", uri=True)
        start_ms, end_ms = get_anki_day_window_ms(target_date)
        row = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(time)/1000.0, 0) FROM revlog WHERE id >= ? AND id < ?",
            (start_ms, end_ms),
        ).fetchone()
        reviews = int(row[0] or 0)
        seconds = float(row[1] or 0.0)
        minutes = round(seconds / 60.0, 1)

        due_remaining = 0
        if is_today:
            crt_row = conn.execute("SELECT crt FROM col").fetchone()
            crt = int(crt_row[0]) if crt_row else 0
            today_anki_day = int((time.time() - crt) // 86400)
            learning_due = conn.execute("SELECT COUNT(*) FROM cards WHERE queue IN (1, 3)").fetchone()[0]
            review_due = conn.execute(
                "SELECT COUNT(*) FROM cards WHERE queue = 2 AND due <= ?",
                (today_anki_day,),
            ).fetchone()[0]
            due_remaining = int(learning_due or 0) + int(review_due or 0)
            done = reviews > 0 and due_remaining == 0
            if done:
                detail = f"{reviews} cards • {minutes:.0f}m (0 due)"
            elif reviews > 0:
                detail = f"{reviews} done ({minutes:.0f}m) • {due_remaining} due left"
            else:
                detail = f"0 cards today • {due_remaining} due"
        else:
            # For historical days, count as done if >= 50 cards reviewed (filtering out accidental 1-card opens)
            done = reviews >= 50
            detail = f"{reviews} cards • {minutes:.0f}m" if reviews > 0 else "0 cards"

        conn.close()
        return {
            "id": "anki",
            "title": "Anki",
            "done": done,
            "auto": True,
            "detail": detail,
            "reviews": reviews,
            "minutes": minutes,
            "due_remaining": due_remaining,
        }
    except Exception as exc:
        return {
            "id": "anki",
            "title": "Anki",
            "done": False,
            "auto": True,
            "detail": f"DB error: {exc}",
            "reviews": 0,
            "minutes": 0.0,
            "due_remaining": 0,
        }


# ---------------------------------------------------------------------------
# 2. MAJOR (GROW) PROOF OF WORK
# ---------------------------------------------------------------------------
def load_grow_profile(user: Optional[str] = None) -> Dict[str, Any]:
    if user is None:
        user = "diomedes"
        try:
            cfg = json.loads(GROW_CONFIG.read_text(encoding="utf-8"))
            user = cfg.get("user", "diomedes")
        except Exception:
            pass
    state_file = GROW_USERS_DIR / f"{user}.json"
    if not state_file.exists():
        state_file = GROW_USERS_DIR / "diomedes.json"
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except Exception:
        return {}


def load_grow_state(user: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
    profile = load_grow_profile(user)
    active_track = profile.get("active_track", "devops")
    tracks = profile.get("tracks", {})
    if not isinstance(active_track, str) or not isinstance(tracks, dict):
        return "devops", {}
    progress = tracks.get(active_track, {})
    return active_track, progress if isinstance(progress, dict) else {}


def check_grow(
    d: Optional[datetime.date] = None,
    state: Optional[Tuple[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    target_date = d or today_date()
    d_str = target_date.isoformat()
    track_id, progress = state if state is not None else load_grow_state()

    logs = [entry for entry in progress.get("daily_logs", []) if entry.get("date") == d_str]
    cur_day = progress.get("current_day", 1)

    if logs:
        last_log = logs[-1]
        day_num = last_log.get("day", cur_day)
        score = last_log.get("quiz_score", 100)
        xp = sum(e.get("xp_earned", 0) for e in logs)
        detail = f"{track_id.upper()} Day {day_num} • {score}% (+{xp} XP)"
        return {
            "id": "major",
            "title": "Study Major (grow)",
            "done": True,
            "auto": True,
            "detail": detail,
            "track": track_id,
            "day": day_num,
            "score": score,
            "note": last_log.get("note", ""),
        }

    # Check completed_days fallback
    for day_key, info in progress.get("completed_days", {}).items():
        if info.get("completed_at") == d_str:
            score = info.get("quiz_score", 100)
            detail = f"{track_id.upper()} Day {day_key} • {score}%"
            return {
                "id": "major",
                "title": "Study Major (grow)",
                "done": True,
                "auto": True,
                "detail": detail,
                "track": track_id,
                "day": int(day_key),
                "score": score,
                "note": info.get("note", ""),
            }

    detail = f"{track_id.upper()} Day {cur_day} pending (`grow`)"
    return {
        "id": "major",
        "title": "Study Major (grow)",
        "done": False,
        "auto": True,
        "detail": detail,
        "track": track_id,
        "day": cur_day,
        "score": None,
        "note": "",
    }


# ---------------------------------------------------------------------------
# 3. SPEAKING (SPEAK) PROOF OF WORK
# ---------------------------------------------------------------------------
def get_speak_db_path(scoped: bool = False) -> Optional[Path]:
    if SPEAK_DIOMEDES_DB.is_file():
        return SPEAK_DIOMEDES_DB
    fallback = SPEAK_PERSONAL_DIOMEDES_DB if scoped else SPEAK_LOCAL_DB
    return fallback if fallback.is_file() else None


def check_speak(d: Optional[datetime.date] = None, *, scoped: bool = False) -> Dict[str, Any]:
    target_date = d or today_date()
    d_str = target_date.isoformat()
    db_path = get_speak_db_path(scoped)

    if not db_path:
        return {
            "id": "speak",
            "title": "Practice Speaking (speak)",
            "done": False,
            "auto": True,
            "detail": "speak.db not found",
            "attempts": 0,
            "duration_sec": 0.0,
        }

    try:
        conn = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
        en_rows = []
        try:
            en_rows = conn.execute(
                "SELECT duration_sec, wpm, review_json FROM attempts WHERE substr(created_at, 1, 10) = ? ORDER BY id DESC",
                (d_str,),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table: attempts" not in str(exc):
                raise

        ja_rows = []
        try:
            ja_rows = conn.execute(
                "SELECT duration_sec, cpm, review_json FROM ja_attempts WHERE substr(created_at, 1, 10) = ? ORDER BY id DESC",
                (d_str,),
            ).fetchall()
        except Exception:
            pass

        total_attempts = len(en_rows) + len(ja_rows)
        if total_attempts > 0:
            total_sec = sum(float(r[0] or 0) for r in en_rows) + sum(float(r[0] or 0) for r in ja_rows)
            mins = total_sec / 60.0
            if en_rows:
                wpm = float(en_rows[0][1] or 0)
                score_str = ""
                try:
                    rev = json.loads(en_rows[0][2] or "{}")
                    if rev.get("score") is not None:
                        score_str = f" • Score {rev['score']}"
                except Exception:
                    pass
                detail = f"{mins:.1f}m • {wpm:.0f} WPM{score_str}"
            else:
                cpm = float(ja_rows[0][1] or 0)
                detail = f"JA {mins:.1f}m • {cpm:.0f} CPM"
            conn.close()
            return {
                "id": "speak",
                "title": "Practice Speaking (speak)",
                "done": True,
                "auto": True,
                "detail": detail,
                "attempts": total_attempts,
                "duration_sec": round(total_sec, 1),
            }

        mission_row = conn.execute(
            "SELECT id, source_title FROM missions WHERE date = ? ORDER BY id DESC LIMIT 1",
            (d_str,),
        ).fetchone()
        conn.close()
        if mission_row:
            detail = f"Mission #{mission_row[0]} ready (`speak`)"
        else:
            detail = "Run `speak` to record today's take"
        return {
            "id": "speak",
            "title": "Practice Speaking (speak)",
            "done": False,
            "auto": True,
            "detail": detail,
            "attempts": 0,
            "duration_sec": 0.0,
        }
    except Exception as exc:
        return {
            "id": "speak",
            "title": "Practice Speaking (speak)",
            "done": False,
            "auto": True,
            "detail": f"DB error: {exc}",
            "attempts": 0,
            "duration_sec": 0.0,
        }


# ---------------------------------------------------------------------------
# 4. READ IN ENGLISH (SELF-TRACKED TIME)
# ---------------------------------------------------------------------------
def check_reading(d: Optional[datetime.date] = None, reading_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    target_date = d or today_date()
    d_str = target_date.isoformat()
    rdata = reading_data if reading_data is not None else load_reading_data()
    entry = rdata.get("days", {}).get(d_str, {})
    minutes = int(entry.get("minutes", 0) or 0)
    note = str(entry.get("note", "") or "").strip()

    done = minutes > 0
    if done:
        detail = f"{minutes}m spent today" + (f" • {note}" if note else "")
    else:
        detail = "0m logged (`pillars read <m>`)"

    return {
        "id": "read",
        "title": "Read in English",
        "done": done,
        "auto": False,
        "detail": detail,
        "minutes": minutes,
        "note": note,
    }


# ---------------------------------------------------------------------------
# AGGREGATION & HISTORY
# ---------------------------------------------------------------------------
def activity_dates(values: Iterable[Any]) -> Set[datetime.date]:
    dates = set()
    for value in values:
        if isinstance(value, str):
            try:
                dates.add(datetime.date.fromisoformat(value))
            except ValueError:
                pass
    return dates


def sqlite_activity_dates(path: Optional[Path], *queries: str) -> Set[datetime.date]:
    dates = set()
    if path is None or not path.is_file():
        return dates
    try:
        with closing(sqlite3.connect(f"{path.as_uri()}?immutable=1", uri=True)) as connection:
            for query in queries:
                try:
                    dates.update(activity_dates(row[0] for row in connection.execute(query)))
                except sqlite3.OperationalError as exc:
                    if "no such table:" not in str(exc):
                        raise
    except sqlite3.Error as exc:
        raise HistoryError(f"Could not read activity dates from {path.name}. {exc}") from exc
    return dates


def gather_local_history() -> Dict[str, Any]:
    days: Dict[str, Dict[str, Any]] = {}
    for date in sqlite_activity_dates(
        ANKI_DB,
        "SELECT DISTINCT date(id / 1000, 'unixepoch', 'localtime', '-4 hours') FROM revlog",
    ):
        activity = check_anki(date)
        if activity["reviews"]:
            days.setdefault(date.isoformat(), {})["anki"] = activity

    profile = load_grow_profile("diomedes")
    active_track = profile.get("active_track", "devops")
    tracks = profile.get("tracks", {})
    if isinstance(tracks, dict):
        for track in sorted(tracks, key=lambda track: (track != active_track, track)):
            progress = tracks[track]
            if not isinstance(progress, dict):
                continue
            dates = activity_dates(entry.get("date") for entry in progress.get("daily_logs", []))
            dates.update(activity_dates(entry.get("completed_at") for entry in progress.get("completed_days", {}).values()))
            for date in dates:
                activity = check_grow(date, (track, progress))
                if activity["done"]:
                    days.setdefault(date.isoformat(), {}).setdefault("major", activity)

    for date in sqlite_activity_dates(
        get_speak_db_path(scoped=True),
        "SELECT DISTINCT substr(created_at, 1, 10) FROM attempts",
        "SELECT DISTINCT substr(created_at, 1, 10) FROM ja_attempts",
    ):
        activity = check_speak(date, scoped=True)
        if activity["attempts"]:
            days.setdefault(date.isoformat(), {})["speak"] = activity

    return {"version": 1, "user": "diomedes", "reading": load_local_reading_data(), "days": days}


def get_day_summary(
    d: Optional[datetime.date] = None,
    reading_data: Optional[Dict[str, Any]] = None,
    synced_history: Optional[Dict[str, Any]] = None,
    grow_state: Optional[Tuple[str, Dict[str, Any]]] = None,
    *,
    scoped: Optional[bool] = None,
) -> Dict[str, Any]:
    target_date = d or today_date()
    if synced_history is None:
        synced_history = load_history(DATA_DIR)
    if reading_data is None:
        reading_data = merge_reading(load_local_reading_data(), synced_history["reading"])
    if scoped is None:
        scoped = history_is_configured(DATA_DIR)
    if grow_state is None:
        grow_state = load_grow_state("diomedes" if scoped else None)
    recorded = synced_history["days"].get(target_date.isoformat(), {})
    items = [
        check_anki(target_date),
        check_grow(target_date, grow_state),
        check_speak(target_date, scoped=scoped),
        check_reading(target_date, reading_data),
    ]
    items = [prefer_activity(item, recorded[item["id"]]) if item["id"] in recorded else item for item in items]
    done_count = sum(1 for it in items if it["done"])
    return {
        "date": target_date.isoformat(),
        "done_count": done_count,
        "total_count": 4,
        "all_done": done_count == 4,
        "pillars": items,
    }


def compute_history_and_streaks(days_back: int = 28) -> Tuple[int, Dict[str, int], List[Dict[str, Any]]]:
    """Returns (full_4_of_4_streak, per_pillar_streaks, last_N_days_summaries)."""
    synced_history = load_history(DATA_DIR)
    rdata = merge_reading(load_local_reading_data(), synced_history["reading"])
    scoped = history_is_configured(DATA_DIR)
    grow_state = load_grow_state("diomedes" if scoped else None)
    today = today_date()
    history: List[Dict[str, Any]] = []
    for offset in range(days_back - 1, -1, -1):
        d = today - datetime.timedelta(days=offset)
        history.append(get_day_summary(d, rdata, synced_history, grow_state, scoped=scoped))

    # Compute full 4/4 streak (counting today if 4/4, else starting from yesterday)
    full_streak = 0
    for idx in range(len(history) - 1, -1, -1):
        day_sum = history[idx]
        if idx == len(history) - 1 and not day_sum["all_done"]:
            continue
        if day_sum["all_done"]:
            full_streak += 1
        else:
            break

    per_pillar: Dict[str, int] = {"anki": 0, "major": 0, "speak": 0, "read": 0}
    for pid in per_pillar:
        st = 0
        for idx in range(len(history) - 1, -1, -1):
            p_map = {p["id"]: p["done"] for p in history[idx]["pillars"]}
            if idx == len(history) - 1 and not p_map.get(pid, False):
                continue
            if p_map.get(pid, False):
                st += 1
            else:
                break
        per_pillar[pid] = st

    return full_streak, per_pillar, history


# ---------------------------------------------------------------------------
# CLI COMMANDS
# ---------------------------------------------------------------------------
def cmd_read(args: argparse.Namespace) -> None:
    raw = (args.minutes or "").strip()
    rdata = load_local_reading_data()
    synced = load_history(DATA_DIR)["reading"]
    d_str = args.date.isoformat() if args.date else today_str()
    cur_entry = merge_reading(rdata, synced)["days"].get(d_str, {})
    cur_mins = int(cur_entry.get("minutes", 0) or 0)

    if raw.lower() in ("reset", "clear", "0"):
        new_mins = 0
    elif raw.startswith("+"):
        try:
            new_mins = max(0, cur_mins + int(raw[1:]))
        except ValueError:
            print(f"{RED}Invalid minutes value: {raw}{RESET}")
            sys.exit(1)
    elif raw.startswith("-"):
        try:
            new_mins = max(0, cur_mins - int(raw[1:]))
        except ValueError:
            print(f"{RED}Invalid minutes value: {raw}{RESET}")
            sys.exit(1)
    else:
        try:
            new_mins = max(0, int(raw))
        except ValueError:
            print(f"{RED}Usage: `pillars read <minutes>` (e.g. `pillars read 35`, `pillars read +15`, or `pillars read reset`){RESET}")
            sys.exit(1)

    note = args.note if args.note is not None else cur_entry.get("note", "")
    rdata["days"][d_str] = {
        "minutes": new_mins,
        "note": note,
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="microseconds"),
    }
    save_reading_data(rdata)

    if args.json:
        cmd_json(args)
        return

    if new_mins > 0:
        note_part = f" [{note}]" if note else ""
        print(f"{FG_GREEN}●{RESET} Logged {BOLD}{FG_WHITE}{new_mins}m{RESET} of English reading for {d_str}{FG_MUTED}{note_part}{RESET}")
    else:
        print(f"{FG_AMBER}○{RESET} Cleared English reading time for {d_str}")


def cmd_json(_args: argparse.Namespace) -> None:
    full_streak, per_pillar, history = compute_history_and_streaks(14)
    today_sum = history[-1]
    payload = {
        "date": today_sum["date"],
        "doneCount": today_sum["done_count"],
        "totalCount": today_sum["total_count"],
        "allDone": today_sum["all_done"],
        "fullStreak": full_streak,
        "pillarStreaks": per_pillar,
        "pillars": today_sum["pillars"],
        "recentDays": [
            {"date": h["date"], "doneCount": h["done_count"]}
            for h in history[-7:]
        ],
    }
    print(json.dumps(payload, ensure_ascii=False))


def cmd_user(args: argparse.Namespace) -> None:
    if args.user_action == "set":
        set_history_user(DATA_DIR, args.user)
    if history_is_configured(DATA_DIR):
        print("Active user is diomedes. History sync is enabled.")
    else:
        print("This install stores history locally. Run `pillars user set diomedes` to enable Git sync.")


def cmd_sync(args: argparse.Namespace) -> None:
    action = args.action or "push"
    if action == "disable":
        disable_schedule()
        print("Disabled automatic history sync.")
        return
    if not history_is_configured(DATA_DIR):
        raise HistoryError("Set up history sync first with `pillars user set diomedes`.")
    if action == "enable":
        enable_schedule(ROOT_DIR / "pillars.py", "fetch" if args.fetch_only else "push")
        mode = "fetch-only sync" if args.fetch_only else "push and fetch"
        print(f"Scheduled automatic {mode} every 15 minutes and queued the first run.")
        return
    local_history = gather_local_history() if action == "push" else None
    history = sync_history(DATA_DIR, action, local_history)
    date_count = len(history["days"].keys() | history["reading"]["days"].keys())
    verb = "Pushed" if action == "push" else "Fetched"
    print(f"{verb} diomedes history covering {date_count} days.")


def _strip_ansi(s: str) -> str:
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


def _pad(s: str, width: int, align: str = "left") -> str:
    vis = len(_strip_ansi(s))
    diff = max(0, width - vis)
    if align == "right":
        return (" " * diff) + s
    if align == "center":
        left = diff // 2
        return (" " * left) + s + (" " * (diff - left))
    return s + (" " * diff)


def cmd_status(_args: argparse.Namespace) -> None:
    DAYS_SPAN = 35
    full_streak, per_pillar, history = compute_history_and_streaks(DAYS_SPAN)
    today_sum = history[-1]
    done_c = today_sum["done_count"]
    W = 96  # Larger inner width

    def box_line(content: str = "") -> str:
        return f"  {FG_BORDER}│{RESET}{_pad(content, W)}{FG_BORDER}│{RESET}"

    def divider(left: str = "├", right: str = "┤", fill: str = "─") -> str:
        return f"  {FG_BORDER}{left}{fill * W}{right}{RESET}"

    dt_obj = datetime.date.fromisoformat(today_sum["date"])
    day_label = dt_obj.strftime("%a %Y-%m-%d").upper()

    # Segmented 4-block progress bar using strict 1-cell glyphs (■ / ·)
    seg_bar = " ".join(
        f"{FG_GREEN}■{RESET}" if i < done_c else f"{FG_BORDER}·{RESET}"
        for i in range(4)
    )
    pct = int((done_c / 4) * 100)
    status_color = FG_GREEN if done_c == 4 else (FG_CYAN if done_c >= 2 else FG_AMBER)

    print()
    print(f"  {FG_BORDER}╭{'─' * W}╮{RESET}")
    print(box_line(""))

    # Header row
    left_hdr = f"   {BOLD}{FG_WHITE}PILLARS{RESET}  {FG_BORDER}//{RESET}  {FG_MUTED}DAILY PROOF OF WORK{RESET}"
    right_hdr = (
        f"{FG_MUTED}{day_label}{RESET}    "
        f"{FG_BORDER}[{RESET} {seg_bar} {FG_BORDER}]{RESET}  "
        f"{BOLD}{status_color}{done_c}/4{RESET} {FG_MUTED}({pct}%){RESET}    "
        f"{FG_MUTED}STREAK{RESET} {BOLD}{FG_GREEN if full_streak > 0 else FG_MUTED}{full_streak}d{RESET}   "
    )
    gap = W - len(_strip_ansi(left_hdr)) - len(_strip_ansi(right_hdr))
    print(box_line(left_hdr + (" " * max(1, gap)) + right_hdr))
    print(box_line(""))
    print(divider())
    print(box_line(""))

    # Column headers
    col_hdr = (
        f"   {FG_MUTED}"
        f"{'STATE':<11}"
        f"{'PILLAR':<28}"
        f"{'MODE':<8}"
        f"{'PROOF OF WORK':<36}"
        f"{'STREAK':>7}"
        f"{RESET}"
    )
    print(box_line(col_hdr))
    print(box_line(f"   {FG_BORDER}{'─' * (W - 6)}{RESET}"))

    short_labels = {
        "anki": "ANKI",
        "major": "MAJOR",
        "speak": "SPEAK",
        "read": "READ",
    }

    for idx, p in enumerate(today_sum["pillars"]):
        pid = p["id"]
        is_done = p["done"]
        state_str = (
            _pad(f"{FG_GREEN}{BOLD}● DONE{RESET}", 11)
            if is_done
            else _pad(f"{FG_AMBER}○ WAIT{RESET}", 11)
        )
        title_color = FG_WHITE if is_done else FG_TEXT
        title_str = _pad(f"{BOLD}{title_color}{p['title'][:26]}{RESET}", 28)
        mode_str = _pad(
            f"{FG_MUTED}AUTO{RESET}" if p["auto"] else f"{FG_CYAN}SELF{RESET}",
            8,
        )

        clean_detail = p["detail"].replace("•", "·").replace("`", "")
        if len(clean_detail) > 35:
            clean_detail = clean_detail[:34] + "…"
        detail_color = FG_CYAN if is_done else FG_MUTED
        detail_str = _pad(f"{detail_color}{clean_detail}{RESET}", 36)

        p_streak = per_pillar.get(pid, 0)
        streak_col = (
            _pad(f"{FG_GREEN}{BOLD}{p_streak}d{RESET}", 7, "right")
            if p_streak > 0
            else _pad(f"{FG_MUTED}0d{RESET}", 7, "right")
        )

        print(box_line(f"   {state_str}{title_str}{mode_str}{detail_str}{streak_col}"))
        if idx < len(today_sum["pillars"]) - 1:
            print(box_line(""))

    print(box_line(""))
    print(divider())
    print(box_line(""))

    # 35-Day Per-Pillar Matrix (fills the 96-column width cleanly)
    first_d = history[0]["date"][5:]
    last_d = history[-1]["date"][5:]
    matrix_hdr_left = f"   {BOLD}{FG_TEXT}35-DAY CONSISTENCY MATRIX{RESET}   {FG_MUTED}({first_d} -> {last_d}){RESET}"
    matrix_hdr_right = f"{FG_GREEN}■{RESET}{FG_MUTED} done   {FG_BORDER}·{RESET}{FG_MUTED} miss{RESET}   "
    mgap = W - len(_strip_ansi(matrix_hdr_left)) - len(_strip_ansi(matrix_hdr_right))
    print(box_line(matrix_hdr_left + (" " * max(1, mgap)) + matrix_hdr_right))
    print(box_line(""))

    for pid in ("anki", "major", "speak", "read"):
        label = _pad(f"{FG_MUTED}{short_labels[pid]}{RESET}", 9)
        cells = []
        hit_count = 0
        for h in history:
            p_done = any(it["id"] == pid and it["done"] for it in h["pillars"])
            if p_done:
                hit_count += 1
                cells.append(f"{FG_GREEN}■{RESET}")
            else:
                cells.append(f"{FG_BORDER}·{RESET}")
        grid_str = " ".join(cells)
        ratio_color = FG_GREEN if hit_count >= 25 else (FG_CYAN if hit_count >= 12 else FG_MUTED)
        ratio_str = _pad(f"{ratio_color}{hit_count:>2}/{DAYS_SPAN}{RESET}", 10, "right")
        print(box_line(f"   {label}{grid_str}   {ratio_str}"))

    # Combined daily intensity row
    tot_label = _pad(f"{BOLD}{FG_TEXT}DAILY{RESET}", 9)
    tot_cells = []
    full_days = 0
    for h in history:
        c = h["done_count"]
        if c == 4:
            full_days += 1
            tot_cells.append(f"{FG_GREEN}█{RESET}")
        elif c == 3:
            tot_cells.append(f"{FG_CYAN}▆{RESET}")
        elif c in (1, 2):
            tot_cells.append(f"{FG_AMBER}▃{RESET}")
        else:
            tot_cells.append(f"{FG_BORDER}·{RESET}")
    tot_grid = " ".join(tot_cells)
    tot_ratio = _pad(f"{FG_MUTED}{full_days:>2} 4/4{RESET}", 10, "right")
    print(box_line(f"   {FG_BORDER}{'─' * (W - 6)}{RESET}"))
    print(box_line(f"   {tot_label}{tot_grid}   {tot_ratio}"))
    print(box_line(""))

    print(divider())
    footer = (
        f"   {FG_MUTED}CMD >{RESET}  "
        f"{FG_CYAN}pillars read <min> [-n note]{RESET}  {FG_BORDER}·{RESET}  "
        f"{FG_CYAN}pillars read +15{RESET}  {FG_BORDER}·{RESET}  "
        f"{FG_TEXT}grow{RESET}  {FG_BORDER}·{RESET}  "
        f"{FG_TEXT}speak{RESET}  {FG_BORDER}·{RESET}  "
        f"{FG_TEXT}anki{RESET}"
    )
    print(box_line(footer))
    print(f"  {FG_BORDER}╰{'─' * W}╯{RESET}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pillars",
        description="4-Pillar Daily Accountability Tracker (Anki, Major/grow, Speaking/speak, Read in English)",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="Show today's 4 pillars dashboard, streaks & 28-day heatmap")
    sub.add_parser("json", help="Output today's 4-pillar state as JSON (used by QuickShell widget)")

    p_read = sub.add_parser("read", help="Log minutes spent reading in English today")
    p_read.add_argument("minutes", help="Minutes read (e.g. '35', '+15', '-10', or 'reset')")
    p_read.add_argument("-n", "--note", help="Optional book title or note", default=None)
    p_read.add_argument("-d", "--date", type=datetime.date.fromisoformat, help="Optional YYYY-MM-DD date (defaults to today)")
    p_read.add_argument("--json", action="store_true", help="Output updated JSON state after logging")

    p_user = sub.add_parser("user", help="Set or show the active history user")
    user_actions = p_user.add_subparsers(dest="user_action")
    set_user = user_actions.add_parser("set", help="Enable Git history sync for diomedes")
    set_user.add_argument("user", choices=("diomedes",))
    user_actions.add_parser("status", help="Show the active history user")

    p_sync = sub.add_parser("sync", help="Sync diomedes history")
    actions = p_sync.add_subparsers(dest="action")
    actions.add_parser("push", help="Fetch, merge, and push history (default)")
    actions.add_parser("fetch", help="Fetch history")
    enable = actions.add_parser("enable", help="Start automatic sync every 15 minutes with systemd")
    enable.add_argument("--fetch-only", action="store_true", help="Schedule fetching without pushing")
    actions.add_parser("disable", help="Stop automatic sync")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    commands = {"status": cmd_status, "json": cmd_json, "read": cmd_read, "user": cmd_user, "sync": cmd_sync}
    try:
        commands[args.command or "status"](args)
    except HistoryError as exc:
        parser.exit(1, f"pillars: {exc}\n")


if __name__ == "__main__":
    main()
