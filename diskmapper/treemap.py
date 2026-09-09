"""Squarified treemap layout (Bruls, Huizing & van Wijk, 2000).

Produces rectangles whose aspect ratios stay close to 1 so the result reads
like a floor plan / blueprint instead of thin slivers.
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

Rect = Tuple[float, float, float, float]  # x, y, w, h


def _worst_ratio(row: Sequence[float], side: float) -> float:
    """Worst aspect ratio produced by laying ``row`` along ``side``."""
    if not row or side <= 0:
        return float("inf")
    row_sum = sum(row)
    if row_sum <= 0:
        return float("inf")
    side_sq = side * side
    row_sq = row_sum * row_sum
    return max(side_sq * max(row) / row_sq, row_sq / (side_sq * min(row)))


def _layout_row(row: Sequence[float], x: float, y: float, w: float, h: float,
                horizontal: bool) -> Tuple[List[Rect], float, float, float, float]:
    """Place one row and return the rects plus the remaining free rectangle."""
    row_sum = sum(row)
    rects: List[Rect] = []
    if row_sum <= 0:
        return rects, x, y, w, h

    if horizontal:
        # Row occupies a full-width band at the top of the free area.
        band = row_sum / h if h > 0 else 0.0
        band = min(band, w)
        offset = y
        for value in row:
            frac = value / row_sum
            rect_h = h * frac
            rects.append((x, offset, band, rect_h))
            offset += rect_h
        return rects, x + band, y, max(w - band, 0.0), h

    band = row_sum / w if w > 0 else 0.0
    band = min(band, h)
    offset = x
    for value in row:
        frac = value / row_sum
        rect_w = w * frac
        rects.append((offset, y, rect_w, band))
        offset += rect_w
    return rects, x, y + band, w, max(h - band, 0.0)


def squarify(values: Sequence[float], x: float, y: float,
             width: float, height: float) -> List[Rect]:
    """Map ``values`` onto rectangles filling the given area.

    ``values`` must be in descending order for best results. The returned list
    is index-aligned with ``values``; zero/negative values get empty rects.
    """
    rects: List[Rect] = [(x, y, 0.0, 0.0)] * len(values)
    positive = [(i, float(v)) for i, v in enumerate(values) if v and v > 0]
    if not positive or width <= 0 or height <= 0:
        return rects

    total = sum(v for _, v in positive)
    area = width * height
    scale = area / total
    scaled = [(i, v * scale) for i, v in positive]

    cx, cy, cw, ch = x, y, width, height
    idx = 0
    row_idx: List[int] = []
    row_val: List[float] = []

    while idx < len(scaled):
        side = min(cw, ch)
        if side <= 0:
            break
        i, value = scaled[idx]
        candidate = row_val + [value]
        if not row_val or _worst_ratio(candidate, side) <= _worst_ratio(row_val, side):
            row_val = candidate
            row_idx.append(i)
            idx += 1
            continue

        horizontal = cw >= ch
        placed, cx, cy, cw, ch = _layout_row(row_val, cx, cy, cw, ch, horizontal)
        for j, rect in zip(row_idx, placed):
            rects[j] = rect
        row_idx, row_val = [], []

    if row_val:
        horizontal = cw >= ch
        placed, cx, cy, cw, ch = _layout_row(row_val, cx, cy, cw, ch, horizontal)
        for j, rect in zip(row_idx, placed):
            rects[j] = rect

    return rects
