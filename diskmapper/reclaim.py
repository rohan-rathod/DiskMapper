"""Reclaim engine: find space you can safely get back, and get it back safely.

Three layers:
  1. Rule-based junk detection (build caches, package caches, temp, dumps).
  2. Content-hash duplicate detection.
  3. A guarded deleter that only ever moves things to the Recycle Bin.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from .categories import categorize, classify_risk, days_old
from .model import Node, human_size
from .scanner import ScanCancel

MB = 1024 * 1024
GB = 1024 * MB


@dataclass
class Finding:
    """One reclaimable item shown in the Reclaim panel."""

    path: str
    name: str
    size: int
    rule: str
    reason: str
    safety: str = "Safe"            # Safe | Review | Advisory
    is_dir: bool = False
    selected: bool = False
    extra: List[str] = field(default_factory=list)

    @property
    def deletable(self) -> bool:
        return self.safety != "Advisory"


# --------------------------------------------------------------------------
# Rule-based junk detection
# --------------------------------------------------------------------------

# (rule name, folder names matched, reason, safety)
_DIR_RULES: Tuple[Tuple[str, Tuple[str, ...], str, str], ...] = (
    ("Build artefacts", ("node_modules",),
     "JavaScript dependencies - restore with 'npm install'", "Safe"),
    ("Build artefacts", ("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"),
     "Python bytecode/test caches - regenerated automatically", "Safe"),
    ("Build artefacts", ("target", "obj", "bin", "build", "dist", ".next", ".nuxt"),
     "Compiler output - rebuilt on next build", "Review"),
    ("Package caches", ("cache", "caches", "cache2", "cachestorage"),
     "Application cache - the app will rebuild it", "Safe"),
    ("Package caches", (".gradle", ".m2", ".nuget", ".cargo", ".ivy2", ".pub-cache"),
     "Downloaded package cache - re-downloaded on demand", "Review"),
    ("Temp files", ("temp", "tmp", "crashdumps", "crashreports"),
     "Temporary/crash data - safe to clear", "Safe"),
    ("Browser data", ("code cache", "gpucache", "service worker", "webcache", "inetcache"),
     "Browser cache - rebuilt while you browse", "Safe"),
    ("Recycle Bin", ("$recycle.bin",),
     "Already-deleted files still occupying disk", "Review"),
    ("Windows Update", ("softwaredistribution",),
     "Downloaded update packages - Windows re-downloads if needed", "Review"),
)

_NEVER_TOUCH = (
    "\\windows\\system32", "\\windows\\syswow64", "\\windows\\winsxs",
    "\\windows\\servicing", "\\windows\\boot", "\\system volume information",
    "\\windows\\assembly", "\\windows\\fonts", "\\recovery",
    "\\program files\\windowsapps", "\\windows\\installer",
)

_ADVISORY_EXT = {".vhdx", ".vhd", ".vmdk", ".vdi", ".qcow2"}
_INSTALLER_EXT = {".exe", ".msi", ".msix", ".msu", ".iso", ".zip", ".7z", ".rar"}


def _protected(path: str) -> bool:
    lowered = path.lower()
    return any(marker in lowered for marker in _NEVER_TOUCH)


def find_junk(root: Node, cancel: Optional[ScanCancel] = None,
              min_size: int = 8 * MB,
              stale_days: int = 180) -> List[Finding]:
    """Walk a scanned tree and return everything worth reclaiming."""
    cancel = cancel or ScanCancel()
    now = time.time()
    findings: List[Finding] = []
    claimed: List[str] = []

    def already_covered(path: str) -> bool:
        lowered = path.lower() + "\\"
        return any(lowered.startswith(c) for c in claimed)

    for node in root.walk():
        cancel.check()
        path = node.path
        if not path or _protected(path):
            continue

        lowered_name = node.name.lower()

        if node.is_dir:
            if node.size < min_size or already_covered(path):
                continue
            for rule, names, reason, safety in _DIR_RULES:
                if lowered_name in names:
                    findings.append(Finding(
                        path=path, name=node.name, size=node.size, rule=rule,
                        reason=reason, safety=safety, is_dir=True,
                        extra=[f"{node.file_count:,} files"]))
                    claimed.append(path.lower() + "\\")
                    break
            continue

        # ---- files ----
        if node.size < min_size or already_covered(path):
            continue
        ext = os.path.splitext(lowered_name)[1]
        age = days_old(node.mtime, now)

        if ext in _ADVISORY_EXT:
            findings.append(Finding(
                path=path, name=node.name, size=node.size,
                rule="Virtual disks", safety="Advisory",
                reason="VM/WSL/Docker disk image - shrink it from the app that owns it, "
                       "never delete it directly",
                extra=[f"last used {age:.0f} days ago"]))
            continue

        if ext in (".log", ".dmp", ".etl", ".chk", ".tmp", ".old", ".bak", ".part"):
            findings.append(Finding(
                path=path, name=node.name, size=node.size, rule="Temp files",
                reason="Log/dump/temp file", safety="Safe",
                extra=[f"{age:.0f} days old"]))
            continue

        if ext in _INSTALLER_EXT and "\\downloads\\" in path.lower() and age > 90:
            findings.append(Finding(
                path=path, name=node.name, size=node.size, rule="Old installers",
                reason=f"Installer/archive in Downloads, untouched for {age:.0f} days",
                safety="Review", extra=[categorize(node.name, False)]))
            continue

        if age > stale_days and node.size > 100 * MB:
            risk = classify_risk(path, node.name, False)
            if risk in ("User Data", "App Data"):
                findings.append(Finding(
                    path=path, name=node.name, size=node.size, rule="Stale large files",
                    reason=f"{human_size(node.size)} untouched for {age / 365.0:.1f} years",
                    safety="Review",
                    extra=[categorize(node.name, False), risk]))

    findings.sort(key=lambda f: f.size, reverse=True)
    return findings


# --------------------------------------------------------------------------
# Duplicate detection
# --------------------------------------------------------------------------

def _hash_file(path: str, partial: bool = True, chunk: int = 256 * 1024) -> Optional[str]:
    digest = hashlib.blake2b(digest_size=16)
    try:
        with open(path, "rb") as handle:
            if partial:
                digest.update(handle.read(chunk))
                handle.seek(-min(chunk, os.path.getsize(path)), os.SEEK_END)
                digest.update(handle.read(chunk))
            else:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
    except (OSError, ValueError):
        return None
    return digest.hexdigest()


def find_duplicates(root: Node, cancel: Optional[ScanCancel] = None,
                    min_size: int = 4 * MB, max_candidates: int = 6000,
                    progress: Optional[Callable[[str, int, int], None]] = None
                    ) -> List[Finding]:
    """Find byte-identical files, keeping the oldest copy of each group.

    Three passes: group by size, then by a cheap head+tail hash, then confirm
    with a full hash. Only the redundant copies become findings.
    """
    cancel = cancel or ScanCancel()

    by_size: Dict[int, List[Node]] = {}
    for node in root.walk():
        cancel.check()
        if node.is_dir or node.size < min_size or _protected(node.path):
            continue
        by_size.setdefault(node.size, []).append(node)

    candidates = [group for group in by_size.values() if len(group) > 1]
    candidates.sort(key=lambda g: g[0].size, reverse=True)

    findings: List[Finding] = []
    checked = 0
    for group in candidates:
        cancel.check()
        if checked > max_candidates:
            break

        partial_groups: Dict[str, List[Node]] = {}
        for node in group:
            cancel.check()
            checked += 1
            digest = _hash_file(node.path, partial=True)
            if digest:
                partial_groups.setdefault(digest, []).append(node)
            if progress and checked % 50 == 0:
                progress(node.name, checked, len(findings))

        for maybe in partial_groups.values():
            if len(maybe) < 2:
                continue
            full_groups: Dict[str, List[Node]] = {}
            for node in maybe:
                cancel.check()
                digest = _hash_file(node.path, partial=False)
                if digest:
                    full_groups.setdefault(digest, []).append(node)

            for identical in full_groups.values():
                if len(identical) < 2:
                    continue
                identical.sort(key=lambda n: n.mtime or 0.0)
                keeper = identical[0]
                for copy in identical[1:]:
                    findings.append(Finding(
                        path=copy.path, name=copy.name, size=copy.size,
                        rule="Duplicates",
                        reason=f"Identical to {keeper.path}",
                        safety="Review",
                        extra=[f"{len(identical)} copies", categorize(copy.name, False)]))

    findings.sort(key=lambda f: f.size, reverse=True)
    return findings


# --------------------------------------------------------------------------
# Safe deletion (Recycle Bin only)
# --------------------------------------------------------------------------

class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", ctypes.c_uint),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


_FO_DELETE = 0x0003
_FOF_SILENT = 0x0004
_FOF_NOCONFIRMATION = 0x0010
_FOF_ALLOWUNDO = 0x0040
_FOF_NOERRORUI = 0x0400
_FOF_WANTNUKEWARNING = 0x4000


def send_to_recycle_bin(paths: Iterable[str]) -> Tuple[int, List[str]]:
    """Move paths to the Recycle Bin. Returns (moved_count, errors).

    Deliberately never performs a permanent delete - everything stays undoable
    from Explorer.
    """
    targets = [os.path.abspath(p) for p in paths
               if p and os.path.exists(p) and not _protected(p)]
    if not targets:
        return 0, ["Nothing valid to delete"]

    errors: List[str] = []
    moved = 0
    try:
        shell32 = ctypes.windll.shell32
    except AttributeError:
        return 0, ["Recycle Bin API unavailable on this platform"]

    # Delete one at a time so a single failure doesn't abort the batch.
    for target in targets:
        buffer = target + "\0\0"
        op = _SHFILEOPSTRUCTW(
            hwnd=None, wFunc=_FO_DELETE, pFrom=buffer, pTo=None,
            fFlags=_FOF_ALLOWUNDO | _FOF_NOCONFIRMATION | _FOF_SILENT | _FOF_NOERRORUI,
            fAnyOperationsAborted=False, hNameMappings=None, lpszProgressTitle=None)
        result = shell32.SHFileOperationW(ctypes.byref(op))
        if result == 0 and not op.fAnyOperationsAborted:
            moved += 1
        else:
            errors.append(f"{os.path.basename(target)} (code {result})")
    return moved, errors


def group_findings(findings: List[Finding]) -> List[Tuple[str, int, List[Finding]]]:
    """Group findings by rule, sorted by reclaimable bytes descending."""
    buckets: Dict[str, List[Finding]] = {}
    for finding in findings:
        buckets.setdefault(finding.rule, []).append(finding)
    summary = [(rule, sum(f.size for f in items), items) for rule, items in buckets.items()]
    summary.sort(key=lambda row: row[1], reverse=True)
    return summary
