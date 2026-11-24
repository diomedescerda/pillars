# pillars

Pillars tracks daily Anki reviews, Grow study, Speak practice, and English reading.

## Sync Diomedes’s history

Diomedes’s history is tracked at `data/users/diomedes.json` on this repository’s `main` branch. Select `diomedes` with the same `user set` command as in Speak and Grow. The dashboard shows the history that shipped with the clone.

```sh
./pillars.py user set diomedes
./pillars.py status
```

Pillars automatically syncs after you log or clear reading time. If the connection fails, the reading entry is saved locally and can sync later. Use `./pillars.py user set diomedes --no-auto-sync` to turn off uploads after reading.

Run `./pillars.py sync` or `./pillars.py sync push` to sync on demand. Run `./pillars.py sync fetch` to download updates from `origin/main`. New installs can fetch without credentials while this repository is public. Pushing requires Git credentials with write access and a configured Git author. The GitHub CLI can configure HTTPS credentials with `gh auth setup-git` after login.

Sync pushes a commit containing just the Diomedes snapshot. It merges daily summaries from Anki, Grow, and Speak with reading entries and their notes. The latest reading edit for each date wins, including resets. Fetch saves the full history to the local `data/history.json` cache and restores the recent activity and streaks shown by `status` and `json`. Source databases stay on the machine where they were created.

### Background sync

On Linux with a systemd user session, enable the timer once per install after selecting `diomedes`.

```sh
./pillars.py sync enable
```

The timer starts a sync immediately, runs every 15 minutes, and catches up after downtime. It uses the install's configured Git credentials to fetch, merge, and push. On an install that only needs to download history, use `./pillars.py sync enable --fetch-only`. That also turns off uploads after reading.

Use `./pillars.py sync disable` to stop the timer. View sync results with `journalctl --user -u pillars-sync.service`.
