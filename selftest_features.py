"""Headless checks for the history, reclaim and natural-language engines."""

from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from diskmapper import query as q
from diskmapper.categories import age_bucket, categorize, classify_risk
from diskmapper.model import human_size
from diskmapper.reclaim import (MB, find_duplicates, find_junk, group_findings,
                                send_to_recycle_bin)
from diskmapper.scanner import scan_directory
from diskmapper.storage import HistoryStore, build_delta_tree, summarize_delta

failures = []


def check(label, condition, extra=""):
    if condition:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label} {extra}")
        failures.append(label)


def write(path, size, age_days=0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(b"D" * size)
    if age_days:
        old = time.time() - age_days * 86400
        os.utime(path, (old, old))


print("== categories ==")
check("video detected", categorize("movie.mp4", False) == "Video")
check("code detected", categorize("app.py", False) == "Code")
check("installer detected", categorize("setup.msi", False) == "Installer")
check("vhdx is disk image", categorize("wsl.vhdx", False) == "Disk Image")
check("node_modules is cache dir", categorize("node_modules", True) == "Cache / Temp")
check("system32 is critical",
      classify_risk(r"C:\Windows\System32\ntdll.dll", "ntdll.dll") == "System Critical")
check("temp is reclaimable",
      classify_risk(r"C:\Users\me\AppData\Local\Temp\x.tmp", "x.tmp") == "Reclaimable")
check("documents are user data",
      classify_risk(r"C:\Users\me\Documents\cv.docx", "cv.docx") == "User Data")
check("age bucket today", age_bucket(time.time())[0] == "Today")
check("age bucket old", age_bucket(time.time() - 800 * 86400)[0] == "Older")

print("== natural language query ==")
cases = [
    ("videos bigger than 500mb", lambda p: p.min_size == 500 * MB and "Video" in p.categories),
    ("images smaller than 2mb", lambda p: p.max_size == 2 * MB and "Image" in p.categories),
    ("what's safe to delete that I haven't touched in a year",
     lambda p: "Reclaimable" in p.risks and p.older_than_days and p.older_than_days > 360),
    ("code files modified in the last 7 days",
     lambda p: p.newer_than_days == 7 and "Code" in p.categories),
    ("top 20 installers older than 6 months",
     lambda p: p.limit == 20 and "Installer" in p.categories and p.older_than_days > 180),
    ("archives bigger than 1gb", lambda p: p.min_size == 1024 ** 3 and "Archive" in p.categories),
    ("folders bigger than 2gb", lambda p: p.want_dirs and p.min_size == 2 * 1024 ** 3),
    ("stale large files older than 2 years", lambda p: p.older_than_days > 700),
]
for text, predicate in cases:
    parsed = q.parse(text)
    check(f'parse: "{text}"', predicate(parsed), f"-> {parsed.describe()}")
check("empty query is detected", q.parse("").is_empty)

with tempfile.TemporaryDirectory() as tmp:
    print("== reclaim rules ==")
    write(os.path.join(tmp, "proj", "node_modules", "pkg", "lib.js"), 12 * MB)
    write(os.path.join(tmp, "proj", "src", "main.py"), 2 * MB)
    write(os.path.join(tmp, "proj", "__pycache__", "main.pyc"), 9 * MB)
    write(os.path.join(tmp, "Downloads", "installer_v1.exe"), 20 * MB, age_days=200)
    write(os.path.join(tmp, "Downloads", "fresh.exe"), 15 * MB, age_days=2)
    write(os.path.join(tmp, "logs", "debug.log"), 11 * MB, age_days=30)
    write(os.path.join(tmp, "vm", "ubuntu.vhdx"), 30 * MB)

    tree = scan_directory(tmp)
    findings = find_junk(tree, min_size=8 * MB)
    rules = {f.rule for f in findings}
    names = {f.name for f in findings}
    check("node_modules flagged", "node_modules" in names, str(names))
    check("__pycache__ flagged", "__pycache__" in names)
    check("old installer flagged", "installer_v1.exe" in names)
    check("recent installer NOT flagged", "fresh.exe" not in names)
    check("log flagged", "debug.log" in names)
    check("source code NOT flagged", "main.py" not in names)
    check("vhdx is advisory only",
          any(f.name == "ubuntu.vhdx" and f.safety == "Advisory" for f in findings))
    check("advisory is not deletable",
          all(not f.deletable for f in findings if f.name == "ubuntu.vhdx"))
    groups = group_findings(findings)
    check("groups sorted by size", groups == sorted(groups, key=lambda g: g[1], reverse=True))
    check("rules discovered", len(rules) >= 3, str(rules))
    total = sum(f.size for f in findings if f.deletable)
    print(f"  -> reclaimable: {human_size(total)} across {len(findings)} findings")

    print("== duplicate detection ==")
    payload = os.urandom(5 * MB)
    for name in ("dup_a.bin", "copies/dup_b.bin", "copies/deep/dup_c.bin"):
        target = os.path.join(tmp, "dupes", name)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(payload)
    other = os.path.join(tmp, "dupes", "unique.bin")
    with open(other, "wb") as handle:
        handle.write(os.urandom(5 * MB))

    dup_tree = scan_directory(os.path.join(tmp, "dupes"))
    dups = find_duplicates(dup_tree, min_size=1 * MB)
    check("two redundant copies found", len(dups) == 2, f"got {len(dups)}")
    check("unique file not flagged", all(d.name != "unique.bin" for d in dups))
    check("reclaimable equals one copy each", sum(d.size for d in dups) == 10 * MB)

    print("== safe delete guard rails ==")
    moved, errors = send_to_recycle_bin([r"C:\Windows\System32\ntdll.dll"])
    check("protected path refused", moved == 0 and bool(errors), str(errors))
    victim = os.path.join(tmp, "trash_me.bin")
    write(victim, 1024)
    moved, errors = send_to_recycle_bin([victim])
    check("real file recycled", moved == 1 and not os.path.exists(victim), str(errors))

    print("== scan history and growth delta ==")
    db_path = os.path.join(tmp, "hist.db")
    store = HistoryStore(db_path)
    snap1 = store.save(tree, mode="folder", label="before")

    write(os.path.join(tmp, "proj", "src", "huge_new.bin"), 40 * MB)
    os.remove(os.path.join(tmp, "logs", "debug.log"))
    tree2 = scan_directory(tmp)
    snap2 = store.save(tree2, mode="folder", label="after")

    snaps = store.list_snapshots()
    check("two snapshots stored", len(snaps) == 2, str(len(snaps)))
    check("snapshot totals differ", snaps[0]["total_size"] != snaps[1]["total_size"])

    delta = build_delta_tree(store.entries(snap1), store.entries(snap2), tmp)
    tags = {c.tag for c in delta.children}
    grew = [c for c in delta.children if c.delta > 0]
    shrank = [c for c in delta.children if c.delta < 0]
    check("growth detected", any(t in tags for t in ("grew", "added")), str(tags))
    check("shrink detected", len(shrank) >= 1, str(tags))
    check("delta blocks sized by magnitude", delta.size == sum(abs(c.delta) for c in delta.children))
    check("net summary reads correctly",
          "grew" in summarize_delta(snaps[1]["total_size"], snaps[0]["total_size"]))
    print(f"  -> {len(grew)} grew, {len(shrank)} shrank; "
          f"net {summarize_delta(snaps[1]['total_size'], snaps[0]['total_size'])}")

    store.delete_snapshot(snap1)
    check("snapshot deletion works", len(store.list_snapshots()) == 1)
    store.close()

    print("== query execution ==")
    results, parsed = q.ask(tree2, "installers bigger than 10mb")
    check("query returns installers", results and all(r.name.endswith(".exe") for r in results),
          str([r.name for r in results]))
    results2, _ = q.ask(tree2, "top 3 files bigger than 1mb")
    check("limit respected", len(results2) <= 3)
    results3, _ = q.ask(tree2, "folders bigger than 1mb")
    check("folder mode returns dirs", results3 and all(r.is_dir for r in results3))

print()
if failures:
    print(f"FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("ALL FEATURE CHECKS PASSED")
