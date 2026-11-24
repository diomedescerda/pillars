import datetime
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[1]


class SyncTest(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.remote = self.root / "history.git"
        self.today = datetime.datetime.now(datetime.timezone.utc).date()
        self.environment = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Pillars Tests",
            "GIT_AUTHOR_EMAIL": "pillars@example.invalid",
            "GIT_COMMITTER_NAME": "Pillars Tests",
            "GIT_COMMITTER_EMAIL": "pillars@example.invalid",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "TZ": "UTC",
        }
        self.run_git(self.root, "init", "--quiet", "--bare", "--initial-branch=main", str(self.remote))
        seed = self.root / "seed"
        seed.mkdir()
        for file in (*PROJECT.glob("*.py"), PROJECT / ".gitignore", PROJECT / "README.md"):
            shutil.copy2(file, seed)
        self.run_git(seed, "init", "--quiet", "--initial-branch=main")
        self.run_git(seed, "add", ".")
        self.run_git(seed, "commit", "--quiet", "-m", "Seed application")
        self.run_git(seed, "push", "--quiet", str(self.remote), "main")

    def run_git(self, directory, *args):
        result = subprocess.run(
            ["git", *args],
            cwd=directory,
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, f"git {' '.join(args)}\n{result.stdout}{result.stderr}")
        return result.stdout

    def run_cli(self, install, *args):
        result = subprocess.run(
            [sys.executable, "pillars.py", *args],
            cwd=install,
            env={**self.environment, "HOME": str(install / "home")},
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, f"{' '.join(args)}\n{result.stdout}{result.stderr}")
        return result.stdout

    def install(self, name):
        install = self.root / name
        self.run_git(self.root, "clone", "--quiet", str(self.remote), str(install))
        (install / "home").mkdir()
        self.run_cli(install, "user", "set", "diomedes")
        return install

    def add_auto_pillar_history(self, install):
        home = install / "home"
        yesterday = self.today - datetime.timedelta(days=1)
        anki = home / ".local/share/Anki2/ユーザー 1/collection.anki2"
        anki.parent.mkdir(parents=True)
        with sqlite3.connect(anki) as connection:
            connection.executescript("""
                CREATE TABLE revlog (id INTEGER PRIMARY KEY, time INTEGER);
                CREATE TABLE col (crt INTEGER);
                CREATE TABLE cards (queue INTEGER, due INTEGER);
                INSERT INTO col VALUES (0);
            """)
            for day, reviews in ((yesterday, 50), (self.today, 2)):
                instant = datetime.datetime.combine(day, datetime.time(5), datetime.timezone.utc)
                milliseconds = int(instant.timestamp() * 1000)
                connection.executemany(
                    "INSERT INTO revlog VALUES (?, 1000)",
                    ((milliseconds + review,) for review in range(reviews)),
                )

        grow = home / "personal/growing/data/users/diomedes.json"
        grow.parent.mkdir(parents=True)
        grow.write_text(json.dumps({
            "active_track": "devops",
            "tracks": {"devops": {"daily_logs": [
                {"date": yesterday.isoformat(), "day": 1, "quiz_score": 80},
                {"date": self.today.isoformat(), "day": 2, "quiz_score": 90},
            ]}},
        }), encoding="utf-8")

        speak = home / "work/Speak/data/diomedes/speak.db"
        speak.parent.mkdir(parents=True)
        with sqlite3.connect(speak) as connection:
            connection.executescript("""
                CREATE TABLE attempts (
                    id INTEGER PRIMARY KEY, created_at TEXT, duration_sec REAL,
                    wpm REAL, review_json TEXT
                );
                CREATE TABLE missions (id INTEGER PRIMARY KEY, date TEXT, source_title TEXT);
            """)
            connection.executemany(
                "INSERT INTO attempts VALUES (?, ?, 60, 100, '{}')",
                ((i, day.isoformat() + "T08:00:00") for i, day in enumerate((yesterday, self.today))),
            )

    def test_fetch_restores_all_four_pillars_without_source_databases(self):
        writer = self.install("writer")
        reader = self.install("reader")
        self.add_auto_pillar_history(writer)
        yesterday = (self.today - datetime.timedelta(days=1)).isoformat()
        self.run_cli(writer, "read", "15", "--date", yesterday)
        self.run_cli(writer, "read", "25", "--note", "Test book")
        self.run_cli(writer, "sync")
        self.run_cli(reader, "sync", "fetch")
        fresh_install = self.install("fresh")

        restored = json.loads(self.run_cli(reader, "json"))
        self.assertEqual(json.loads(self.run_cli(fresh_install, "json")), restored)
        tracked_files = self.run_git(fresh_install, "ls-files").splitlines()
        self.assertEqual([file for file in tracked_files if file.startswith("data/")], ["data/users/diomedes.json"])
        self.assertEqual(restored["doneCount"], 4)
        self.assertEqual(restored["fullStreak"], 2)
        self.assertIn({"date": yesterday, "doneCount": 4}, restored["recentDays"])
        pillars = {pillar["id"]: pillar for pillar in restored["pillars"]}
        self.assertEqual(pillars["anki"]["reviews"], 2)
        self.assertEqual(pillars["major"]["score"], 90)
        self.assertEqual(pillars["speak"]["duration_sec"], 60.0)
        self.assertEqual(pillars["read"]["minutes"], 25)
        self.assertEqual(pillars["read"]["note"], "Test book")

    def test_push_merges_reading_from_other_install_and_keeps_reset(self):
        writer = self.install("writer")
        other = self.install("other")
        reader = self.install("reader")
        yesterday = (self.today - datetime.timedelta(days=1)).isoformat()
        two_days_ago = (self.today - datetime.timedelta(days=2)).isoformat()
        self.run_cli(writer, "read", "12", "--date", two_days_ago)
        self.run_cli(writer, "read", "20")
        self.run_cli(writer, "sync", "push")

        self.run_cli(other, "read", "30", "--date", yesterday)
        self.run_cli(other, "read", "reset")
        self.run_cli(other, "sync", "push")
        self.run_cli(reader, "sync", "fetch")

        restored = json.loads(self.run_cli(reader, "json"))
        self.assertEqual(restored["doneCount"], 0)
        self.assertIn({"date": yesterday, "doneCount": 1}, restored["recentDays"])
        self.assertIn({"date": two_days_ago, "doneCount": 1}, restored["recentDays"])


if __name__ == "__main__":
    unittest.main()
