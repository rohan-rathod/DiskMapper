"""Scan history: persist snapshots to SQLite and diff them over time.

This is what turns DiskMapper from a one-shot utility into something you open
every week: "what actually grew since Monday?"
"""

from __future__ import annotations

import os
import sqlite3
import time
from typing import Dict, List, Optional, Tuple

from .model import Node

# Only folders down to this depth are persisted. Keeps snapshots small
# (a few thousand rows) while still being precise enough to explain growth.
SNAPSHOT_DEPTH = 7


def default_db_path() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "DiskMapper")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "history.db")


class HistoryStore:
    """SQLite-backed store of past scans."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or default_db_path()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_schema()

    def _create_schema(self) -> None:
        with self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS snapshots (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    root        TEXT NOT NULL,
                    label       TEXT NOT NULL,
                    mode        TEXT NOT NULL DEFAULT 'folder',
                    taken_at    REAL NOT NULL,
                    total_size  INTEGER NOT NULL,
                    file_count  INTEGER NOT NULL DEFAULT 0
                )""")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS entries (
                    snapshot_id INTEGER NOT NULL,
                    path        TEXT NOT NULL,
                    name        TEXT NOT NULL,
                    size        INTEGER NOT NULL,
                    is_dir      INTEGER NOT NULL,
                    depth       INTEGER NOT NULL,
                    PRIMARY KEY (snapshot_id, path)
                ) WITHOUT ROWID""")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_snap_root ON snapshots(root, taken_at DESC)")

    # -- writing ---------------------------------------------------------
    def save(self, root: Node, mode: str = "folder", label: str = "") -> int:
        """Persist a scan and return its snapshot id."""
        taken = time.time()
        label = label or time.strftime("%Y-%m-%d %H:%M", time.localtime(taken))
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO snapshots (root, label, mode, taken_at, total_size, file_count)"
                " VALUES (?,?,?,?,?,?)",
                (root.path or root.name, label, mode, taken, root.size, root.file_count))
            snap_id = int(cur.lastrowid)

            rows: List[Tuple] = []
            stack: List[Tuple[Node, int]] = [(root, 0)]
            while stack:
                node, depth = stack.pop()
                key = node.path or node.name
                rows.append((snap_id, key, node.name, node.size,
                             1 if node.is_dir else 0, depth))
                if depth < SNAPSHOT_DEPTH:
                    for child in node.children:
                        if child.is_dir or depth < 2:
                            stack.append((child, depth + 1))

            self._conn.executemany(
                "INSERT OR REPLACE INTO entries "
                "(snapshot_id, path, name, size, is_dir, depth) VALUES (?,?,?,?,?,?)",
                rows)
        return snap_id

    # -- reading ---------------------------------------------------------
    def list_snapshots(self, root: Optional[str] = None, limit: int = 60) -> List[dict]:
        sql = ("SELECT id, root, label, mode, taken_at, total_size, file_count"
               " FROM snapshots")
        params: Tuple = ()
        if root:
            sql += " WHERE root = ?"
            params = (root,)
        sql += " ORDER BY taken_at DESC LIMIT ?"
        params = params + (limit,)
        cur = self._conn.execute(sql, params)
        return [dict(zip(("id", "root", "label", "mode", "taken_at",
                          "total_size", "file_count"), row))
                for row in cur.fetchall()]

    def entries(self, snapshot_id: int) -> Dict[str, Tuple[str, int, int]]:
        """Return {path: (name, size, is_dir)} for a snapshot."""
        cur = self._conn.execute(
            "SELECT path, name, size, is_dir FROM entries WHERE snapshot_id = ?",
            (snapshot_id,))
        return {row[0]: (row[1], row[2], row[3]) for row in cur.fetchall()}

    def delete_snapshot(self, snapshot_id: int) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM entries WHERE snapshot_id = ?", (snapshot_id,))
            self._conn.execute("DELETE FROM snapshots WHERE id = ?", (snapshot_id,))

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:
            pass


# --------------------------------------------------------------------------
# Diffing
# --------------------------------------------------------------------------

def build_delta_tree(old: Dict[str, Tuple[str, int, int]],
                     new: Dict[str, Tuple[str, int, int]],
                     root_path: str) -> Node:
    """Build a tree whose ``size`` is |bytes changed| and ``delta`` is signed.

    Nodes are tagged ``grew``, ``shrank``, ``added`` or ``removed`` so the UI
    can paint a red/green growth map.
    """
    all_paths = set(old) | set(new)
    changes: Dict[str, Tuple[str, int, str]] = {}

    for path in all_paths:
        old_name, old_size, old_dir = old.get(path, ("", 0, 0))
        new_name, new_size, new_dir = new.get(path, ("", 0, 0))
        diff = new_size - old_size
        if diff == 0:
            continue
        if path not in old:
            tag = "added"
        elif path not in new:
            tag = "removed"
        else:
            tag = "grew" if diff > 0 else "shrank"
        name = new_name or old_name or os.path.basename(path) or path
        changes[path] = (name, diff, tag)

    base_name = os.path.basename(root_path.rstrip("\\/")) or root_path
    root = Node(name=f"Changes in {base_name}",
                path=root_path, is_dir=True)
    root.detail = "growth map"

    if not changes:
        return root

    # Keep only the most specific changed nodes: if a folder changed *and* one
    # of its descendants changed, show the descendant, otherwise the folder's
    # delta would double-count its children in the treemap.
    has_changed_descendant = set()
    for path in changes:
        parent = os.path.dirname(path)
        while parent and len(parent) >= len(root_path):
            if parent in changes:
                has_changed_descendant.add(parent)
            nxt = os.path.dirname(parent)
            if nxt == parent:
                break
            parent = nxt

    kept = {path: payload for path, payload in changes.items()
            if path not in has_changed_descendant}
    if not kept:
        kept = changes

    for path, (name, diff, tag) in kept.items():
        node = Node(name=name, path=path, size=abs(diff),
                    is_dir=bool(new.get(path, old.get(path, ("", 0, 0)))[2]))
        node.delta = diff
        node.tag = tag
        sign = "+" if diff > 0 else "-"
        node.detail = f"{tag}  {sign}{abs(diff):,} bytes"
        root.add(node)

    root.size = sum(c.size for c in root.children)
    root.sort_recursive()
    return root


def summarize_delta(old_total: int, new_total: int) -> str:
    diff = new_total - old_total
    if diff == 0:
        return "No net change"
    sign = "grew" if diff > 0 else "shrank"
    pct = (abs(diff) * 100.0 / old_total) if old_total else 0.0
    return f"{sign} by {abs(diff):,} bytes ({pct:.1f}%)"
