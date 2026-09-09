"""Data model shared by the scanners and the UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Node:
    """A file, folder or installed application in the size tree."""

    name: str
    path: str
    size: int = 0
    is_dir: bool = False
    children: List["Node"] = field(default_factory=list)
    parent: Optional["Node"] = None
    error: bool = False
    detail: str = ""
    mtime: float = 0.0          # newest modification time in this subtree
    delta: int = 0              # bytes changed vs. a comparison snapshot
    tag: str = ""               # reclaim/insight marker

    def add(self, child: "Node") -> None:
        child.parent = self
        self.children.append(child)

    def sort_recursive(self) -> None:
        stack = [self]
        while stack:
            node = stack.pop()
            if node.children:
                node.children.sort(key=lambda n: n.size, reverse=True)
                stack.extend(node.children)

    @property
    def file_count(self) -> int:
        if not self.children:
            return 0 if self.is_dir else 1
        return sum(c.file_count for c in self.children)

    def percent_of(self, other: "Node") -> float:
        if not other or other.size <= 0:
            return 0.0
        return self.size * 100.0 / other.size

    def breadcrumb(self) -> List["Node"]:
        chain: List["Node"] = []
        node: Optional["Node"] = self
        while node is not None:
            chain.append(node)
            node = node.parent
        chain.reverse()
        return chain


    def walk(self):
        """Yield every node in this subtree, parents before children."""
        stack = [self]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(node.children)


def human_size(num_bytes: float) -> str:
    """Format a byte count the way Windows Explorer does."""
    if num_bytes is None:
        return "-"
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(value) < 1024.0 or unit == "PB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:,.1f} {unit}"
        value /= 1024.0
    return f"{value:,.1f} PB"
