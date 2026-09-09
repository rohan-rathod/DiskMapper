"""Scanning engines: filesystem walker and Windows installed-application reader."""

from __future__ import annotations

import os
import string
import threading
from typing import Callable, Dict, List, Optional, Tuple

from .model import Node

ProgressFn = Callable[[str, int, int], None]


class CancelledError(Exception):
    """Raised internally when the user aborts a scan."""


class ScanCancel:
    """Thread-safe cancellation flag shared with the UI."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def reset(self) -> None:
        self._event.clear()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self._event.is_set():
            raise CancelledError()


# --------------------------------------------------------------------------
# Filesystem scanning
# --------------------------------------------------------------------------

def scan_directory(root_path: str, cancel: Optional[ScanCancel] = None,
                   progress: Optional[ProgressFn] = None,
                   max_depth: int = 12) -> Node:
    """Build a size tree for ``root_path``.

    Uses an explicit stack (no recursion limit issues on deep trees), follows
    no symlinks/junctions, and tolerates access-denied folders.
    """
    cancel = cancel or ScanCancel()
    root_path = os.path.abspath(root_path)
    root = Node(name=_display_name(root_path), path=root_path, is_dir=True)

    counters = {"files": 0, "dirs": 0}
    # Each entry: (node, depth, iterator-not-yet-started flag)
    stack: List[Tuple[Node, int]] = [(root, 0)]
    pending: List[Node] = []

    while stack:
        cancel.check()
        node, depth = stack.pop()
        pending.append(node)
        counters["dirs"] += 1

        if progress and counters["dirs"] % 40 == 0:
            progress(node.path, counters["dirs"], counters["files"])

        try:
            with os.scandir(node.path) as it:
                for entry in it:
                    cancel.check()
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            child = Node(name=entry.name,
                                         path=entry.path,
                                         is_dir=True)
                            node.add(child)
                            if depth < max_depth:
                                stack.append((child, depth + 1))
                            else:
                                child.size = _shallow_size(child.path, cancel)
                                child.detail = "depth limit reached"
                        else:
                            stat = entry.stat(follow_symlinks=False)
                            child = Node(name=entry.name, path=entry.path,
                                         size=stat.st_size, is_dir=False,
                                         mtime=stat.st_mtime)
                            node.add(child)
                            counters["files"] += 1
                    except (OSError, ValueError):
                        continue
        except PermissionError:
            node.error = True
            node.detail = "access denied"
        except OSError:
            node.error = True
            node.detail = "unreadable"

    # Roll sizes up from the leaves.
    for node in reversed(pending):
        if node.children:
            node.size += sum(c.size for c in node.children)
            node.mtime = max([node.mtime] + [c.mtime for c in node.children])

    root.sort_recursive()
    if progress:
        progress(root_path, counters["dirs"], counters["files"])
    return root


def _shallow_size(path: str, cancel: ScanCancel) -> int:
    """Total bytes under ``path`` without building nodes (used past max depth)."""
    total = 0
    stack = [path]
    while stack:
        cancel.check()
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def _display_name(path: str) -> str:
    name = os.path.basename(path.rstrip("\\/"))
    return name or path


def list_drives() -> List[str]:
    """Return ready fixed/removable drive roots such as ``C:\\``."""
    drives = []
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        if os.path.exists(root):
            drives.append(root)
    return drives


def drive_usage(path: str) -> Optional[Tuple[int, int, int]]:
    """Return (total, used, free) bytes for the volume holding ``path``."""
    try:
        usage = os.statvfs(path)  # type: ignore[attr-defined]
        total = usage.f_blocks * usage.f_frsize
        free = usage.f_bavail * usage.f_frsize
        return total, total - free, free
    except AttributeError:
        import shutil

        try:
            total, used, free = shutil.disk_usage(path)
            return total, used, free
        except OSError:
            return None
    except OSError:
        return None


# --------------------------------------------------------------------------
# Installed applications (Windows registry)
# --------------------------------------------------------------------------

_UNINSTALL_KEYS = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
)


def scan_installed_apps(cancel: Optional[ScanCancel] = None,
                        progress: Optional[ProgressFn] = None,
                        measure_folders: bool = True) -> Node:
    """Build a tree of installed applications and their on-disk size.

    Size comes from the registry ``EstimatedSize`` value; when that is missing
    and ``measure_folders`` is set, the install folder is measured directly.
    """
    cancel = cancel or ScanCancel()
    root = Node(name="Installed Applications", path="", is_dir=True)

    try:
        import winreg
    except ImportError:  # pragma: no cover - non-Windows fallback
        root.detail = "registry unavailable on this platform"
        return root

    hives = (
        (winreg.HKEY_LOCAL_MACHINE, "HKLM"),
        (winreg.HKEY_CURRENT_USER, "HKCU"),
    )
    seen: Dict[str, Node] = {}
    count = 0

    for hive, hive_name in hives:
        for subkey in _UNINSTALL_KEYS:
            cancel.check()
            try:
                key = winreg.OpenKey(hive, subkey)
            except OSError:
                continue
            with key:
                try:
                    total_subkeys = winreg.QueryInfoKey(key)[0]
                except OSError:
                    continue
                for index in range(total_subkeys):
                    cancel.check()
                    try:
                        app_key_name = winreg.EnumKey(key, index)
                        with winreg.OpenKey(key, app_key_name) as app_key:
                            info = _read_app(winreg, app_key)
                    except OSError:
                        continue
                    if not info:
                        continue

                    name, size, location, version, publisher = info
                    if size <= 0 and measure_folders and location and os.path.isdir(location):
                        size = _shallow_size(location, cancel)

                    count += 1
                    if progress and count % 5 == 0:
                        progress(name, count, 0)

                    key_id = name.lower()
                    if key_id in seen:
                        if size > seen[key_id].size:
                            seen[key_id].size = size
                        continue

                    node = Node(name=name,
                                path=location or f"{hive_name}\\{app_key_name}",
                                size=size,
                                is_dir=bool(location and os.path.isdir(location)))
                    bits = [b for b in (publisher, version) if b]
                    node.detail = " - ".join(bits) if bits else hive_name
                    seen[key_id] = node
                    root.add(node)

    root.size = sum(c.size for c in root.children)
    root.sort_recursive()
    if progress:
        progress("done", count, 0)
    return root


def _read_app(winreg, app_key):
    """Extract (name, size_bytes, location, version, publisher) or None."""

    def value(name: str):
        try:
            return winreg.QueryValueEx(app_key, name)[0]
        except OSError:
            return None

    name = value("DisplayName")
    if not name or not str(name).strip():
        return None
    if value("SystemComponent") == 1:
        return None
    if value("ParentKeyName") or value("ReleaseType") in ("Security Update", "Update", "Hotfix"):
        return None

    estimated = value("EstimatedSize")
    size = int(estimated) * 1024 if isinstance(estimated, int) and estimated > 0 else 0
    location = value("InstallLocation") or ""
    location = str(location).strip().strip('"')
    return (str(name).strip(), size, location,
            str(value("DisplayVersion") or ""), str(value("Publisher") or ""))
