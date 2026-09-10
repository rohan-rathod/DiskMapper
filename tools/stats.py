"""Release analytics dashboard for DiskMapper.

Polls the public GitHub API for download counts, stars and forks, stores every
reading in a local SQLite database, and renders a terminal dashboard showing
current totals plus growth over time.

No third-party packages. No account required for the public numbers.

Usage:
    python tools/stats.py                  # fetch, record, show dashboard
    python tools/stats.py --watch 300      # refresh every 5 minutes
    python tools/stats.py --history        # every recorded reading
    python tools/stats.py --csv out.csv    # export history
    python tools/stats.py --offline        # render last reading, no network

A token is optional and only needed for private traffic data (unique visitors
and clones), which GitHub restricts to repository owners:

    $env:GITHUB_TOKEN = "ghp_..."          # PowerShell
    python tools/stats.py
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_REPO = "rohan-rathod/DiskMapper"
API = "https://api.github.com"
USER_AGENT = "DiskMapper-Stats/1.0"
TIMEOUT = 20

SPARK = "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
# Fallback for consoles that cannot encode the block characters (the classic
# Windows console defaults to cp1252, which raises UnicodeEncodeError).
SPARK_ASCII = "_.-=+*#@"


def db_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "DiskMapper" / "stats.db"


# ---------------------------------------------------------------- data model


@dataclass
class Asset:
    tag: str
    name: str
    downloads: int
    size: int = 0


@dataclass
class Snapshot:
    """One reading of the repository's public counters."""

    ts: str
    stars: int = 0
    forks: int = 0
    watchers: int = 0
    issues: int = 0
    downloads: int = 0
    views: int = 0
    uniques: int = 0
    clones: int = 0
    assets: list[Asset] = field(default_factory=list)
    releases: int = 0


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ------------------------------------------------------------------- network


def _get(url: str, token: str | None) -> object:
    req = urllib.request.Request(url)
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "application/vnd.github+json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch(repo: str, token: str | None = None) -> Snapshot:
    """Read current counters from the GitHub API."""
    snap = Snapshot(ts=_now())

    meta = _get(f"{API}/repos/{repo}", token)
    if isinstance(meta, dict):
        snap.stars = int(meta.get("stargazers_count") or 0)
        snap.forks = int(meta.get("forks_count") or 0)
        snap.watchers = int(meta.get("subscribers_count") or 0)
        snap.issues = int(meta.get("open_issues_count") or 0)

    releases = _get(f"{API}/repos/{repo}/releases?per_page=100", token)
    if isinstance(releases, list):
        snap.releases = len(releases)
        for rel in releases:
            tag = str(rel.get("tag_name") or "?")
            for asset in rel.get("assets") or []:
                count = int(asset.get("download_count") or 0)
                snap.assets.append(
                    Asset(
                        tag=tag,
                        name=str(asset.get("name") or "?"),
                        downloads=count,
                        size=int(asset.get("size") or 0),
                    )
                )
                snap.downloads += count

    if token:
        # Traffic endpoints need push access; ignore them when unavailable.
        try:
            views = _get(f"{API}/repos/{repo}/traffic/views", token)
            if isinstance(views, dict):
                snap.views = int(views.get("count") or 0)
                snap.uniques = int(views.get("uniques") or 0)
            clones = _get(f"{API}/repos/{repo}/traffic/clones", token)
            if isinstance(clones, dict):
                snap.clones = int(clones.get("count") or 0)
        except (urllib.error.HTTPError, urllib.error.URLError):
            pass

    return snap


# ------------------------------------------------------------------- storage


class StatsStore:
    """Append-only history of readings."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else db_path()
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                repo      TEXT NOT NULL,
                ts        TEXT NOT NULL,
                stars     INTEGER NOT NULL DEFAULT 0,
                forks     INTEGER NOT NULL DEFAULT 0,
                watchers  INTEGER NOT NULL DEFAULT 0,
                issues    INTEGER NOT NULL DEFAULT 0,
                downloads INTEGER NOT NULL DEFAULT 0,
                views     INTEGER NOT NULL DEFAULT 0,
                uniques   INTEGER NOT NULL DEFAULT 0,
                clones    INTEGER NOT NULL DEFAULT 0,
                releases  INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS assets (
                snapshot_id INTEGER NOT NULL,
                tag         TEXT NOT NULL,
                name        TEXT NOT NULL,
                downloads   INTEGER NOT NULL DEFAULT 0,
                size        INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_snap_repo_ts
                ON snapshots(repo, ts);
            CREATE INDEX IF NOT EXISTS idx_assets_snap
                ON assets(snapshot_id);
            """
        )
        self.conn.commit()

    def record(self, repo: str, snap: Snapshot) -> int:
        cur = self.conn.execute(
            "INSERT INTO snapshots"
            " (repo, ts, stars, forks, watchers, issues, downloads,"
            "  views, uniques, clones, releases)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                repo,
                snap.ts,
                snap.stars,
                snap.forks,
                snap.watchers,
                snap.issues,
                snap.downloads,
                snap.views,
                snap.uniques,
                snap.clones,
                snap.releases,
            ),
        )
        sid = int(cur.lastrowid or 0)
        for a in snap.assets:
            self.conn.execute(
                "INSERT INTO assets (snapshot_id, tag, name, downloads, size)"
                " VALUES (?,?,?,?,?)",
                (sid, a.tag, a.name, a.downloads, a.size),
            )
        self.conn.commit()
        return sid

    def history(self, repo: str, limit: int = 500) -> list[sqlite3.Row]:
        rows = self.conn.execute(
            "SELECT * FROM snapshots WHERE repo = ? ORDER BY ts DESC LIMIT ?",
            (repo, limit),
        ).fetchall()
        return list(reversed(rows))

    def latest(self, repo: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM snapshots WHERE repo = ? ORDER BY id DESC LIMIT 1",
            (repo,),
        ).fetchone()

    def assets_for(self, snapshot_id: int) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM assets WHERE snapshot_id = ?"
                " ORDER BY downloads DESC",
                (snapshot_id,),
            ).fetchall()
        )

    def close(self) -> None:
        self.conn.close()


# ----------------------------------------------------------------- rendering


def sparkline(values: list[int], width: int = 40) -> str:
    """Render a compact unicode bar chart of a numeric series."""
    if not values:
        return ""
    if len(values) > width:
        # Bucket down to the target width, keeping the shape.
        step = len(values) / width
        picked = []
        for i in range(width):
            chunk = values[int(i * step):int((i + 1) * step)] or [values[-1]]
            picked.append(max(chunk))
        values = picked
    lo, hi = min(values), max(values)
    if hi == lo:
        return SPARK[0] * len(values) if hi == 0 else SPARK[3] * len(values)
    span = hi - lo
    out = []
    for v in values:
        idx = int((v - lo) / span * (len(SPARK) - 1))
        out.append(SPARK[idx])
    return "".join(out)


def _delta_since(rows: list[sqlite3.Row], field_name: str, hours: float) -> int | None:
    """Change in a counter over the last `hours`, or None if no older reading."""
    if not rows:
        return None
    now = datetime.now(timezone.utc)
    current = int(rows[-1][field_name])
    cutoff = now.timestamp() - hours * 3600
    baseline = None
    for row in rows:
        try:
            ts = datetime.fromisoformat(row["ts"]).timestamp()
        except ValueError:
            continue
        if ts <= cutoff:
            baseline = int(row[field_name])
    if baseline is None:
        return None
    return current - baseline


def _fmt_delta(d: int | None) -> str:
    if d is None:
        return "   --"
    if d > 0:
        return f"  +{d}"
    if d < 0:
        return f"  {d}"
    return "    0"


def _human(n: int) -> str:
    step = 1024.0
    val = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if val < step:
            return f"{val:.0f} {unit}" if unit == "B" else f"{val:.1f} {unit}"
        val /= step
    return f"{val:.1f} TB"


def _age(ts: str) -> str:
    try:
        then = datetime.fromisoformat(ts)
    except ValueError:
        return ts
    secs = (datetime.now(timezone.utc) - then).total_seconds()
    if secs < 90:
        return "just now"
    if secs < 5400:
        return f"{secs / 60:.0f} min ago"
    if secs < 172800:
        return f"{secs / 3600:.0f} hours ago"
    return f"{secs / 86400:.0f} days ago"


def _ascii_fallback(text: str) -> str:
    """Downgrade block characters so any console can render the output."""
    table = str.maketrans(dict(zip(SPARK, SPARK_ASCII)))
    return text.translate(table).encode("ascii", "replace").decode("ascii")


def emit(text: str) -> None:
    """Print text, degrading gracefully on consoles with a narrow codec."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(_ascii_fallback(text))


def dashboard(repo: str, store: StatsStore) -> str:
    rows = store.history(repo)
    if not rows:
        return "No readings recorded yet. Run without --offline first."

    cur = rows[-1]
    lines: list[str] = []
    bar = "=" * 62
    lines.append(bar)
    lines.append(f"  DiskMapper release analytics  -  {repo}")
    lines.append(f"  reading taken {_age(cur['ts'])}   ({len(rows)} recorded)")
    lines.append(bar)
    lines.append("")

    metrics = [
        ("Downloads", "downloads"),
        ("Stars", "stars"),
        ("Forks", "forks"),
        ("Watchers", "watchers"),
        ("Open issues", "issues"),
    ]
    if int(cur["uniques"]):
        metrics.append(("Unique visitors (14d)", "uniques"))
        metrics.append(("Clones (14d)", "clones"))

    lines.append(f"  {'METRIC':<22}{'NOW':>8}{'24H':>8}{'7 DAYS':>9}")
    lines.append("  " + "-" * 47)
    for label, key in metrics:
        d1 = _delta_since(rows, key, 24)
        d7 = _delta_since(rows, key, 24 * 7)
        lines.append(
            f"  {label:<22}{int(cur[key]):>8}"
            f"{_fmt_delta(d1):>8}{_fmt_delta(d7):>9}"
        )
    lines.append("")

    series = [int(r["downloads"]) for r in rows]
    if len(series) > 1:
        lines.append("  Downloads over time")
        lines.append(f"    {sparkline(series)}")
        lines.append(f"    {series[0]} -> {series[-1]}"
                     f"   (+{series[-1] - series[0]} total)")
        lines.append("")

    assets = store.assets_for(int(cur["id"]))
    if assets:
        lines.append(f"  {'ASSET':<28}{'TAG':<10}{'SIZE':>10}{'DOWNLOADS':>12}")
        lines.append("  " + "-" * 58)
        for a in assets:
            name = a["name"]
            if len(name) > 27:
                name = name[:24] + "..."
            lines.append(
                f"  {name:<28}{a['tag']:<10}"
                f"{_human(int(a['size'])):>10}{int(a['downloads']):>12}"
            )
        lines.append("")

    total = int(cur["downloads"])
    lines.append("  " + "-" * 58)
    if total == 0:
        lines.append("  No downloads yet. Share the release link to get started:")
        lines.append(f"    https://github.com/{repo}/releases/latest")
    elif total < 25:
        lines.append(f"  {total} downloads. Early days - ask each user for"
                     " feedback directly;")
        lines.append("  at this size individual conversations beat any metric.")
    elif total < 200:
        lines.append(f"  {total} downloads. Worth adding opt-in telemetry now to"
                     " learn which")
        lines.append("  features actually get used, and a Discussions tab for"
                     " feedback.")
    else:
        lines.append(f"  {total} downloads. Real traction - time to consider"
                     " code signing")
        lines.append("  and a paid tier. See README for the distribution ladder.")
    lines.append(bar)
    return "\n".join(lines)


def history_table(repo: str, store: StatsStore) -> str:
    rows = store.history(repo)
    if not rows:
        return "No readings recorded yet."
    out = [f"  {'WHEN':<22}{'DOWNLOADS':>11}{'STARS':>8}{'FORKS':>8}"]
    out.append("  " + "-" * 47)
    for r in rows:
        ts = r["ts"].replace("T", " ").replace("+00:00", "")
        out.append(
            f"  {ts:<22}{int(r['downloads']):>11}"
            f"{int(r['stars']):>8}{int(r['forks']):>8}"
        )
    return "\n".join(out)


def export_csv(repo: str, store: StatsStore, dest: str) -> int:
    rows = store.history(repo)
    cols = ["ts", "downloads", "stars", "forks", "watchers", "issues",
            "views", "uniques", "clones", "releases"]
    with open(dest, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in rows:
            w.writerow([r[c] for c in cols])
    return len(rows)


# ----------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Track DiskMapper release downloads over time.")
    p.add_argument("--repo", default=DEFAULT_REPO,
                   help=f"owner/name (default {DEFAULT_REPO})")
    p.add_argument("--watch", type=int, metavar="SECONDS",
                   help="refresh continuously every N seconds")
    p.add_argument("--history", action="store_true",
                   help="print every recorded reading")
    p.add_argument("--csv", metavar="PATH", help="export history to CSV")
    p.add_argument("--offline", action="store_true",
                   help="render stored data without calling the API")
    p.add_argument("--token", default=os.environ.get("GITHUB_TOKEN"),
                   help="token for private traffic stats (optional)")
    p.add_argument("--db", help="override database location")
    args = p.parse_args(argv)

    # Prefer real UTF-8 output; emit() covers consoles that refuse it.
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, OSError, ValueError):
        pass

    store = StatsStore(args.db)
    try:
        if args.csv:
            n = export_csv(args.repo, store, args.csv)
            print(f"Exported {n} readings to {args.csv}")
            return 0

        if args.history:
            emit(history_table(args.repo, store))
            return 0

        def refresh() -> bool:
            if args.offline:
                return True
            try:
                snap = fetch(args.repo, args.token)
            except urllib.error.HTTPError as e:
                if e.code == 403:
                    print("GitHub rate limit reached (60 requests/hour without"
                          " a token). Showing stored data.\n")
                elif e.code == 404:
                    print(f"Repository {args.repo} not found or private.\n")
                else:
                    print(f"GitHub returned HTTP {e.code}. Showing stored"
                          " data.\n")
                return False
            except urllib.error.URLError as e:
                print(f"No network ({e.reason}). Showing stored data.\n")
                return False
            store.record(args.repo, snap)
            return True

        if args.watch:
            interval = max(60, args.watch)
            try:
                while True:
                    refresh()
                    os.system("cls" if os.name == "nt" else "clear")
                    emit(dashboard(args.repo, store))
                    print(f"\n  refreshing every {interval}s - Ctrl+C to stop")
                    time.sleep(interval)
            except KeyboardInterrupt:
                print("\nStopped.")
            return 0

        refresh()
        emit(dashboard(args.repo, store))
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
