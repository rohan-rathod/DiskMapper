"""Headless checks for the layout, model and scanner logic (no GUI needed)."""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from diskmapper.model import Node, human_size
from diskmapper.scanner import ScanCancel, list_drives, scan_directory, scan_installed_apps
from diskmapper.treemap import squarify

failures = []


def check(label, condition, extra=""):
    if condition:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label} {extra}")
        failures.append(label)


print("== treemap layout ==")
values = [500, 250, 120, 80, 30, 20]
rects = squarify(values, 0, 0, 600, 400)
check("rect count matches input", len(rects) == len(values))
area_total = sum(w * h for _, _, w, h in rects)
check("rectangles fill the canvas area", abs(area_total - 600 * 400) < 1.0,
      f"got {area_total:.2f}")
inside = all(x >= -0.01 and y >= -0.01 and x + w <= 600.01 and y + h <= 400.01
             for x, y, w, h in rects)
check("all rectangles stay inside bounds", inside)
expected_first = 600 * 400 * values[0] / sum(values)
check("largest block area is proportional",
      abs(rects[0][2] * rects[0][3] - expected_first) < 1.0)
check("empty input is safe", squarify([], 0, 0, 100, 100) == [])
check("zero values produce empty rects", squarify([0, 0], 0, 0, 100, 100)[0][2] == 0)

print("== model ==")
check("human_size bytes", human_size(512) == "512 B", human_size(512))
check("human_size MB", human_size(5 * 1024 * 1024) == "5.0 MB", human_size(5 * 1024 * 1024))
check("human_size GB", human_size(3 * 1024 ** 3) == "3.0 GB", human_size(3 * 1024 ** 3))
parent = Node("root", "r", is_dir=True)
kid = Node("kid", "r/k", size=25)
parent.add(kid)
parent.size = 100
check("percent_of", abs(kid.percent_of(parent) - 25.0) < 0.001)
check("breadcrumb chain", [n.name for n in kid.breadcrumb()] == ["root", "kid"])
check("divide-by-zero guarded", kid.percent_of(Node("z", "z")) == 0.0)

print("== filesystem scanner ==")
with tempfile.TemporaryDirectory() as tmp:
    os.makedirs(os.path.join(tmp, "big", "nested"))
    os.makedirs(os.path.join(tmp, "small"))
    with open(os.path.join(tmp, "big", "a.bin"), "wb") as f:
        f.write(b"x" * 4000)
    with open(os.path.join(tmp, "big", "nested", "b.bin"), "wb") as f:
        f.write(b"x" * 1000)
    with open(os.path.join(tmp, "small", "c.bin"), "wb") as f:
        f.write(b"x" * 500)

    tree = scan_directory(tmp)
    check("total size rolled up", tree.size == 5500, f"got {tree.size}")
    check("children sorted largest first", tree.children[0].name == "big")
    check("nested size aggregated", tree.children[0].size == 5000)
    check("file count", tree.file_count == 3, f"got {tree.file_count}")
    check("percent of parent", abs(tree.children[0].percent_of(tree) - 90.909) < 0.01)

    cancel = ScanCancel()
    cancel.cancel()
    try:
        scan_directory(tmp, cancel)
        check("cancellation raises", False)
    except Exception as exc:
        check("cancellation raises", type(exc).__name__ == "CancelledError")

print("== windows integration ==")
drives = list_drives()
check("at least one drive found", len(drives) > 0, str(drives))
apps = scan_installed_apps(measure_folders=False)
check("installed apps discovered", len(apps.children) > 0, f"got {len(apps.children)}")
check("apps total is positive", apps.size > 0)
top = apps.children[0]
print(f"  -> largest app: {top.name} = {human_size(top.size)} "
      f"({top.percent_of(apps):.1f}% of all apps)")

print()
if failures:
    print(f"FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("ALL CHECKS PASSED")
