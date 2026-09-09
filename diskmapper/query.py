"""Natural-language search over a scanned tree.

Runs entirely offline - it compiles plain English into structured predicates,
so there is no API key, no latency and nothing about your filesystem ever
leaves the machine.

Examples that work:
    "videos bigger than 500mb"
    "what's safe to delete that I haven't touched in a year"
    "top 20 installers in downloads older than 6 months"
    "code files modified in the last 7 days"
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from .categories import categorize, classify_risk, days_old
from .model import Node, human_size

_UNITS = {
    "b": 1, "byte": 1, "bytes": 1,
    "k": 1024, "kb": 1024, "kib": 1024,
    "m": 1024 ** 2, "mb": 1024 ** 2, "mib": 1024 ** 2, "meg": 1024 ** 2, "megs": 1024 ** 2,
    "g": 1024 ** 3, "gb": 1024 ** 3, "gib": 1024 ** 3, "gig": 1024 ** 3, "gigs": 1024 ** 3,
    "t": 1024 ** 4, "tb": 1024 ** 4, "tib": 1024 ** 4,
}

_TIME_UNITS = {
    "day": 1.0, "days": 1.0, "d": 1.0,
    "week": 7.0, "weeks": 7.0, "w": 7.0,
    "month": 30.44, "months": 30.44, "mo": 30.44,
    "year": 365.25, "years": 365.25, "yr": 365.25, "yrs": 365.25, "y": 365.25,
}

_CATEGORY_WORDS = {
    "video": "Video", "videos": "Video", "movie": "Video", "movies": "Video", "film": "Video",
    "image": "Image", "images": "Image", "photo": "Image", "photos": "Image",
    "picture": "Image", "pictures": "Image", "screenshot": "Image", "screenshots": "Image",
    "audio": "Audio", "music": "Audio", "song": "Audio", "songs": "Audio", "mp3": "Audio",
    "archive": "Archive", "archives": "Archive", "zip": "Archive", "zips": "Archive",
    "installer": "Installer", "installers": "Installer", "setup": "Installer",
    "iso": "Disk Image", "isos": "Disk Image", "vm": "Disk Image", "vhdx": "Disk Image",
    "document": "Document", "documents": "Document", "docs": "Document", "pdf": "Document",
    "pdfs": "Document", "spreadsheet": "Document",
    "code": "Code", "source": "Code", "script": "Code", "scripts": "Code",
    "database": "Database", "databases": "Database", "db": "Database",
    "cache": "Cache / Temp", "caches": "Cache / Temp", "temp": "Cache / Temp",
    "temporary": "Cache / Temp", "junk": "Cache / Temp", "log": "Cache / Temp",
    "logs": "Cache / Temp",
    "system": "System", "dll": "System", "dlls": "System",
}

_STOPWORDS = {
    "what", "whats", "what's", "which", "show", "me", "find", "list", "get", "give",
    "all", "any", "the", "a", "an", "is", "are", "that", "those", "these", "of", "on",
    "my", "i", "ive", "i've", "have", "havent", "haven't", "hasnt", "hasn't", "not",
    "and", "or", "with", "for", "to", "be", "been", "can", "could", "should", "would",
    "files", "file", "folders", "folder", "items", "stuff", "things", "space", "disk",
    "using", "taking", "up", "it", "them", "there", "here", "please", "biggest",
    "largest", "smallest", "top", "most", "much", "many", "big", "large", "small",
    "old", "older", "new", "newer", "recent", "recently", "safe", "delete", "deleted",
    "remove", "touched", "used", "modified", "changed", "created", "in", "last",
    "within", "past", "than", "over", "under", "more", "less", "at", "least",
    "bigger", "smaller", "greater", "about", "from", "by",
}


@dataclass
class Query:
    """A compiled query: a set of predicates plus a human-readable summary."""

    min_size: Optional[int] = None
    max_size: Optional[int] = None
    older_than_days: Optional[float] = None
    newer_than_days: Optional[float] = None
    categories: List[str] = field(default_factory=list)
    extensions: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    name_terms: List[str] = field(default_factory=list)
    path_terms: List[str] = field(default_factory=list)
    want_dirs: bool = False
    limit: int = 200
    explanation: List[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not any([self.min_size, self.max_size, self.older_than_days,
                        self.newer_than_days, self.categories, self.extensions,
                        self.risks, self.name_terms, self.path_terms, self.want_dirs])

    def describe(self) -> str:
        return "  &  ".join(self.explanation) if self.explanation else "everything, largest first"


_WORD_NUMBERS = {
    "a": 1.0, "an": 1.0, "one": 1.0, "two": 2.0, "three": 3.0, "four": 4.0,
    "five": 5.0, "six": 6.0, "seven": 7.0, "eight": 8.0, "nine": 9.0, "ten": 10.0,
    "couple": 2.0, "few": 3.0, "several": 3.0,
}

_QUANTITY = r"(\d+(?:\.\d+)?|a|an|one|two|three|four|five|six|seven|eight|nine|ten|couple|few|several)"
_SIZE_RE = (r"(\d+(?:\.\d+)?)\s*"
            r"(tb|tib|gb|gib|gigs?|mb|mib|megs?|kb|kib|bytes?|[bkmgt])\b")
_AGE_RE = _QUANTITY + r"\s*(days?|d|weeks?|w|months?|mo|years?|yrs?|y)\b"
_TOP_RE = r"\btop\s+(\d+)"
_EXT_RE = r"(?:^|\s)\.([a-z0-9]{1,6})\b"


def _amount(token: str) -> float:
    token = token.strip()
    if token in _WORD_NUMBERS:
        return _WORD_NUMBERS[token]
    try:
        return float(token)
    except ValueError:
        return 1.0


def _parse_size(text: str) -> Tuple[Optional[int], Optional[int], List[str]]:
    notes: List[str] = []
    min_size = max_size = None

    for match in re.finditer(_SIZE_RE, text):
        value = float(match.group(1))
        unit = _UNITS.get(match.group(2), 1)
        size = int(value * unit)
        before = text[:match.start()]

        if re.search(r"(bigger|larger|greater|more|over|above|exceed\w*|at least|>=|>)\s*\w*\s*$", before):
            min_size = size
            notes.append(f"larger than {human_size(size)}")
        elif re.search(r"(smaller|less|under|below|at most|<=|<)\s*\w*\s*$", before):
            max_size = size
            notes.append(f"smaller than {human_size(size)}")
        else:
            min_size = size
            notes.append(f"at least {human_size(size)}")
    return min_size, max_size, notes


def _parse_age(text: str) -> Tuple[Optional[float], Optional[float], List[str]]:
    notes: List[str] = []
    older = newer = None

    for match in re.finditer(_AGE_RE, text):
        days = _amount(match.group(1)) * _TIME_UNITS.get(match.group(2), 1.0)
        before = text[:match.start()]
        recent = re.search(r"(last|past|within|recent\w*|newer than|since)\s*\w*\s*$", before)
        stale = re.search(r"(older than|more than|over|not (?:touched|used|opened|modified)"
                          r"(?:\s+\w+)*|havent\s+\w+|haven't\s+\w+|untouched\s*\w*|for)\s*\w*\s*$", before)
        if recent and not stale:
            newer = days
            notes.append(f"changed in the last {match.group(0).strip()}")
        else:
            older = days
            notes.append(f"untouched for over {match.group(0).strip()}")

    if older is None and newer is None:
        if re.search(r"\b(today)\b", text):
            newer, notes = 1.0, notes + ["changed today"]
        elif re.search(r"\b(this week|last week)\b", text):
            newer, notes = 7.0, notes + ["changed this week"]
        elif re.search(r"\b(this month)\b", text):
            newer, notes = 31.0, notes + ["changed this month"]
        elif re.search(r"\b(stale|ancient|forgotten|abandoned)\b", text):
            older, notes = 365.0, notes + ["untouched for over a year"]
    return older, newer, notes


def parse(text: str) -> Query:
    """Compile a natural-language string into a :class:`Query`."""
    query = Query()
    lowered = " " + (text or "").lower().strip() + " "
    if not lowered.strip():
        return query

    query.min_size, query.max_size, size_notes = _parse_size(lowered)
    query.explanation.extend(size_notes)

    query.older_than_days, query.newer_than_days, age_notes = _parse_age(lowered)
    query.explanation.extend(age_notes)

    for match in re.finditer(_TOP_RE, lowered):
        query.limit = max(1, min(int(match.group(1)), 5000))
        query.explanation.append(f"top {query.limit}")

    for ext in re.findall(_EXT_RE, lowered):
        query.extensions.append("." + ext)
    if query.extensions:
        query.explanation.append("type " + ", ".join(query.extensions))

    # Blank out everything already consumed so numeric/unit tokens such as
    # "10mb" or "a year" never leak into the free-text name filter.
    residual = lowered
    for pattern in (_SIZE_RE, _AGE_RE, _TOP_RE, _EXT_RE):
        residual = re.sub(pattern, " ", residual)

    if re.search(r"\b(safe to (delete|remove)|junk|reclaim\w*|cleanup|clean up|garbage|"
                 r"can i delete|deletable)\b", lowered):
        query.risks.append("Reclaimable")
        query.explanation.append("classified reclaimable")
    if re.search(r"\b(my (own )?(stuff|data|files)|personal|user data)\b", lowered):
        query.risks.append("User Data")
        query.explanation.append("your own data")

    if re.search(r"\b(folders?|directories|dirs)\b", lowered) and not re.search(r"\bfiles?\b", lowered):
        query.want_dirs = True
        query.explanation.append("folders only")

    in_paths = re.findall(r"\bin\s+([a-z0-9_\-\. ]{2,30}?)(?:\s+(?:older|newer|bigger|larger|"
                          r"smaller|that|which|from|with|over|under|and)\b|\s*$)", residual)
    for candidate in in_paths:
        term = candidate.strip()
        if (term and term not in _STOPWORDS and not term.isdigit()
                and term not in _TIME_UNITS and term not in _UNITS
                and term not in _WORD_NUMBERS):
            query.path_terms.append(term)
    if query.path_terms:
        query.explanation.append("under " + ", ".join(query.path_terms))

    words = re.findall(r"[a-z0-9'\.\-]+", residual)
    for word in words:
        if word in _CATEGORY_WORDS:
            category = _CATEGORY_WORDS[word]
            if category not in query.categories:
                query.categories.append(category)
    if query.categories:
        query.explanation.append("category " + ", ".join(query.categories))

    for phrase in re.findall(r'"([^"]+)"', lowered):
        query.name_terms.append(phrase.strip())
    if not query.name_terms:
        consumed = set(query.path_terms)
        for word in words:
            if (len(word) > 2 and word not in _STOPWORDS and word not in _CATEGORY_WORDS
                    and not re.fullmatch(r"[\d\.]+", word)
                    and word not in _UNITS and word not in _TIME_UNITS
                    and word not in _WORD_NUMBERS
                    and not any(word in term for term in consumed)):
                query.name_terms.append(word)
    if query.name_terms:
        query.explanation.append("name contains " + ", ".join(query.name_terms))

    return query


def run(root: Node, query: Query, now: Optional[float] = None) -> List[Node]:
    """Execute a compiled query against a scanned tree."""
    now = now or time.time()
    results: List[Node] = []

    for node in root.walk():
        if node is root:
            continue
        if query.want_dirs and not node.is_dir:
            continue
        if not query.want_dirs and node.is_dir:
            continue
        if query.min_size is not None and node.size < query.min_size:
            continue
        if query.max_size is not None and node.size > query.max_size:
            continue

        if query.older_than_days is not None or query.newer_than_days is not None:
            if not node.mtime:
                continue
            age = days_old(node.mtime, now)
            if query.older_than_days is not None and age < query.older_than_days:
                continue
            if query.newer_than_days is not None and age > query.newer_than_days:
                continue

        if query.extensions:
            lowered = node.name.lower()
            if not any(lowered.endswith(ext) for ext in query.extensions):
                continue
        if query.categories and categorize(node.name, node.is_dir, node.path) not in query.categories:
            continue
        if query.risks and classify_risk(node.path, node.name, node.is_dir) not in query.risks:
            continue

        lowered_path = node.path.lower()
        if query.path_terms and not any(term in lowered_path for term in query.path_terms):
            continue
        if query.name_terms:
            lowered_name = node.name.lower()
            if not any(term in lowered_name or term in lowered_path for term in query.name_terms):
                continue

        results.append(node)

    results.sort(key=lambda n: n.size, reverse=True)
    return results[:query.limit]


def ask(root: Node, text: str) -> Tuple[List[Node], Query]:
    """Parse ``text`` and run it in one step."""
    query = parse(text)
    return run(root, query), query


SUGGESTIONS: Tuple[str, ...] = (
    "videos bigger than 500mb",
    "what's safe to delete that I haven't touched in a year",
    "top 20 installers in downloads older than 6 months",
    "code files modified in the last 7 days",
    "archives bigger than 1gb",
    "logs and caches over 50mb",
    "stale large files older than 2 years",
    "folders bigger than 2gb",
)
