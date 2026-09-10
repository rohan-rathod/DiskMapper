"""Community data for the in-app Community view.

Reads three public things from the GitHub API - release download counts, the
repository's star count, and issues labelled ``review`` - then turns them into
something worth putting on screen: an average rating, a star distribution, an
update check, and a list of review cards.

Every response is cached locally, so the view still renders when the machine is
offline. Nothing is ever sent to GitHub beyond an ordinary anonymous GET.

No third-party packages.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REPO = "rohan-rathod/DiskMapper"
API = "https://api.github.com"
USER_AGENT = "DiskMapper-Community/1.0"
TIMEOUT = 12
REVIEW_LABEL = "review"

#: Below this many downloads the raw number is framed as early access rather
#: than presented as a headline metric - a small number shown proudly reads as
#: a failure signal to a first-time visitor.
ADOPTION_THRESHOLD = 25

STARS_FULL = "\u2605"
STARS_EMPTY = "\u2606"


def db_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "DiskMapper" / "community.db"


# ------------------------------------------------------------------- model


@dataclass
class Review:
    author: str = "someone"
    rating: int = 0
    title: str = ""
    body: str = ""
    created: str = ""
    url: str = ""

    @property
    def stars(self) -> str:
        r = max(0, min(5, int(self.rating)))
        return STARS_FULL * r + STARS_EMPTY * (5 - r)

    @property
    def initial(self) -> str:
        name = (self.author or "?").strip()
        return name[0].upper() if name else "?"


@dataclass
class Community:
    repo: str = REPO
    version: str = ""
    latest_version: str = ""
    downloads: int = 0
    stars: int = 0
    forks: int = 0
    reviews: list[Review] = field(default_factory=list)
    fetched_at: str = ""
    from_cache: bool = False
    error: str = ""

    @property
    def update_available(self) -> bool:
        if not self.latest_version or not self.version:
            return False
        return compare_versions(self.latest_version, self.version) > 0

    @property
    def rated(self) -> list[Review]:
        return [r for r in self.reviews if r.rating > 0]

    @property
    def average_rating(self) -> float:
        rated = self.rated
        if not rated:
            return 0.0
        return sum(r.rating for r in rated) / len(rated)

    @property
    def distribution(self) -> dict[int, int]:
        out = {n: 0 for n in range(1, 6)}
        for r in self.rated:
            out[max(1, min(5, r.rating))] += 1
        return out

    def stars_display(self) -> str:
        """Average rounded to the nearest half, drawn with full/empty stars."""
        avg = self.average_rating
        full = int(avg + 0.5)
        return STARS_FULL * full + STARS_EMPTY * (5 - full)


# -------------------------------------------------------------- versioning


def _version_tuple(text: str) -> tuple[int, ...]:
    cleaned = re.sub(r"^[vV]", "", (text or "").strip())
    parts = re.split(r"[.\-+]", cleaned)
    nums: list[int] = []
    for p in parts:
        if p.isdigit():
            nums.append(int(p))
        else:
            break
    return tuple(nums) if nums else (0,)


def compare_versions(a: str, b: str) -> int:
    """Return 1 if a is newer than b, -1 if older, 0 if equivalent."""
    ta, tb = _version_tuple(a), _version_tuple(b)
    size = max(len(ta), len(tb))
    ta = ta + (0,) * (size - len(ta))
    tb = tb + (0,) * (size - len(tb))
    if ta > tb:
        return 1
    if ta < tb:
        return -1
    return 0


# ------------------------------------------------------------ review parsing

_RATING_PATTERNS = (
    re.compile(r"rating\s*[:=]\s*([1-5])", re.I),
    re.compile(r"\b([1-5])\s*/\s*5\b"),
    re.compile(r"\b([1-5])\s*(?:out of|of)\s*5\b", re.I),
    re.compile(r"\b([1-5])\s*stars?\b", re.I),
)


def parse_rating(*texts: str) -> int:
    """Pull a 1-5 rating out of a review title or body.

    Recognises an explicit ``Rating: 4`` line, ``4/5``, ``4 out of 5``,
    ``4 stars``, and a run of filled star characters. Returns 0 when the
    review carries no rating at all, so it can be shown but left out of the
    average.
    """
    for text in texts:
        if not text:
            continue
        filled = text.count(STARS_FULL)
        if 1 <= filled <= 5:
            return filled
        for pat in _RATING_PATTERNS:
            m = pat.search(text)
            if m:
                return int(m.group(1))
    return 0


def _strip_template(body: str) -> str:
    """Remove the rating line and HTML comments from a submitted review."""
    text = re.sub(r"<!--.*?-->", "", body or "", flags=re.S)
    lines = []
    for line in text.splitlines():
        if re.match(r"\s*(rating|version|windows)\s*[:=]", line, re.I):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def review_url(repo: str = REPO, version: str = "") -> str:
    """A 'Write a review' link that opens GitHub with the form pre-filled."""
    body = (
        "Rating: 5\n"
        f"Version: {version or 'unknown'}\n"
        "\n"
        "<!-- Change the rating above to 1-5, then tell us: -->\n"
        "What did you use it for?\n\n"
        "What worked well?\n\n"
        "What was confusing or missing?\n"
    )
    params = urllib.parse.urlencode({
        "labels": REVIEW_LABEL,
        "title": "Review: ",
        "body": body,
    })
    return f"https://github.com/{repo}/issues/new?{params}"


def issue_url(repo: str = REPO, version: str = "") -> str:
    """A 'Report a problem' link with the environment section pre-filled."""
    body = (
        f"Version: {version or 'unknown'}\n"
        "\n"
        "What happened?\n\n"
        "What did you expect instead?\n\n"
        "Steps to reproduce:\n1. \n2. \n"
    )
    params = urllib.parse.urlencode({
        "labels": "bug",
        "title": "",
        "body": body,
    })
    return f"https://github.com/{repo}/issues/new?{params}"


# ------------------------------------------------------------------ network


def _get(url: str, timeout: int = TIMEOUT) -> object:
    req = urllib.request.Request(url)
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "application/vnd.github+json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _relative(iso_ts: str) -> str:
    try:
        then = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except ValueError:
        return iso_ts
    secs = (datetime.now(timezone.utc) - then).total_seconds()
    if secs < 3600:
        return "just now" if secs < 300 else f"{secs / 60:.0f} minutes ago"
    if secs < 86400:
        return f"{secs / 3600:.0f} hours ago"
    if secs < 86400 * 30:
        return f"{secs / 86400:.0f} days ago"
    if secs < 86400 * 365:
        return f"{secs / 86400 / 30:.0f} months ago"
    return f"{secs / 86400 / 365:.0f} years ago"


def fetch(repo: str = REPO, version: str = "", timeout: int = TIMEOUT) -> Community:
    """Read the public community signals. Raises nothing; sets .error instead."""
    out = Community(repo=repo, version=version,
                    fetched_at=datetime.now(timezone.utc)
                    .replace(microsecond=0).isoformat())
    try:
        meta = _get(f"{API}/repos/{repo}", timeout)
        if isinstance(meta, dict):
            out.stars = int(meta.get("stargazers_count") or 0)
            out.forks = int(meta.get("forks_count") or 0)

        releases = _get(f"{API}/repos/{repo}/releases?per_page=100", timeout)
        if isinstance(releases, list):
            for i, rel in enumerate(releases):
                if i == 0:
                    out.latest_version = str(rel.get("tag_name") or "")
                for asset in rel.get("assets") or []:
                    out.downloads += int(asset.get("download_count") or 0)

        url = (f"{API}/repos/{repo}/issues"
               f"?labels={REVIEW_LABEL}&state=all&per_page=50")
        issues = _get(url, timeout)
        if isinstance(issues, list):
            for issue in issues:
                if issue.get("pull_request"):
                    continue
                title = str(issue.get("title") or "")
                body = str(issue.get("body") or "")
                user = issue.get("user") or {}
                out.reviews.append(Review(
                    author=str(user.get("login") or "someone"),
                    rating=parse_rating(title, body),
                    title=re.sub(r"^review\s*:\s*", "", title, flags=re.I).strip(),
                    body=_strip_template(body),
                    created=_relative(str(issue.get("created_at") or "")),
                    url=str(issue.get("html_url") or ""),
                ))
    except urllib.error.HTTPError as e:
        if e.code == 403:
            out.error = "GitHub rate limit reached. Try again in a few minutes."
        elif e.code == 404:
            out.error = "Repository not found."
        else:
            out.error = f"GitHub returned HTTP {e.code}."
    except urllib.error.URLError as e:
        out.error = f"No connection ({e.reason})."
    except (ValueError, TypeError) as e:
        out.error = f"Unexpected response ({e})."
    return out


# ------------------------------------------------------------------- cache


class CommunityCache:
    """Last successful payload, so the view works offline."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else db_path()
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS cache ("
            " repo TEXT PRIMARY KEY, fetched_at TEXT, payload TEXT)")
        self.conn.commit()

    def save(self, data: Community) -> None:
        payload = json.dumps({
            "downloads": data.downloads,
            "stars": data.stars,
            "forks": data.forks,
            "latest_version": data.latest_version,
            "fetched_at": data.fetched_at,
            "reviews": [
                {"author": r.author, "rating": r.rating, "title": r.title,
                 "body": r.body, "created": r.created, "url": r.url}
                for r in data.reviews
            ],
        })
        self.conn.execute(
            "INSERT INTO cache (repo, fetched_at, payload) VALUES (?,?,?)"
            " ON CONFLICT(repo) DO UPDATE SET fetched_at=excluded.fetched_at,"
            " payload=excluded.payload",
            (data.repo, data.fetched_at, payload))
        self.conn.commit()

    def load(self, repo: str = REPO, version: str = "") -> Optional[Community]:
        row = self.conn.execute(
            "SELECT payload FROM cache WHERE repo = ?", (repo,)).fetchone()
        if not row:
            return None
        try:
            d = json.loads(row[0])
        except ValueError:
            return None
        out = Community(repo=repo, version=version, from_cache=True)
        out.downloads = int(d.get("downloads") or 0)
        out.stars = int(d.get("stars") or 0)
        out.forks = int(d.get("forks") or 0)
        out.latest_version = str(d.get("latest_version") or "")
        out.fetched_at = str(d.get("fetched_at") or "")
        for r in d.get("reviews") or []:
            out.reviews.append(Review(
                author=str(r.get("author") or "someone"),
                rating=int(r.get("rating") or 0),
                title=str(r.get("title") or ""),
                body=str(r.get("body") or ""),
                created=str(r.get("created") or ""),
                url=str(r.get("url") or ""),
            ))
        return out

    def close(self) -> None:
        self.conn.close()


def load(repo: str = REPO, version: str = "", cache_path: Path | str | None = None,
         online: bool = True) -> Community:
    """Fetch fresh data, falling back to the last cached payload on failure."""
    cache = CommunityCache(cache_path)
    try:
        if online:
            fresh = fetch(repo, version)
            if not fresh.error:
                cache.save(fresh)
                return fresh
            cached = cache.load(repo, version)
            if cached is not None:
                cached.error = fresh.error
                return cached
            return fresh
        cached = cache.load(repo, version)
        return cached if cached is not None else Community(repo=repo,
                                                           version=version)
    finally:
        cache.close()


# ------------------------------------------------------------- presentation


def adoption_line(downloads: int) -> tuple[str, str]:
    """Headline and supporting line for the download count.

    A raw '6 downloads' banner reads as a failure signal to someone opening the
    app for the first time, so below the threshold the same honest number is
    framed as early access - which is what it actually is.
    """
    if downloads <= 0:
        return ("Just released", "Be the first to try it and leave a review.")
    if downloads < ADOPTION_THRESHOLD:
        noun = "download" if downloads == 1 else "downloads"
        return (f"Early access - {downloads} {noun}",
                "You are among the first to use DiskMapper. "
                "Your review shapes what gets built next.")
    if downloads < 1000:
        return (f"{downloads} downloads", "Thanks for being part of it.")
    return (f"{downloads / 1000:.1f}k downloads", "Thanks for being part of it.")


def rating_line(data: Community) -> str:
    rated = data.rated
    if not rated:
        return "No ratings yet"
    noun = "rating" if len(rated) == 1 else "ratings"
    return f"{data.average_rating:.1f} out of 5  -  {len(rated)} {noun}"
