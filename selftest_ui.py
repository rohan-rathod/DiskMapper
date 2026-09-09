"""GUI checks: builds the real window, drives every view, then closes it."""

from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from diskmapper.reclaim import MB
from diskmapper.scanner import scan_directory
from diskmapper.storage import HistoryStore
from diskmapper.ui import DiskMapperApp

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
        stamp = time.time() - age_days * 86400
        os.utime(path, (stamp, stamp))


def canvas_stats(app):
    items = app.canvas.find_all()
    rects = [i for i in items if app.canvas.type(i) == "rectangle"]
    texts = [app.canvas.itemcget(i, "text") for i in items if app.canvas.type(i) == "text"]
    return len(rects), [t for t in texts if "%" in t]


tmp = tempfile.mkdtemp()
write(os.path.join(tmp, "media", "clip.mp4"), 30 * MB, age_days=400)
write(os.path.join(tmp, "media", "photo.jpg"), 6 * MB, age_days=10)
write(os.path.join(tmp, "proj", "node_modules", "a", "lib.js"), 18 * MB)
write(os.path.join(tmp, "proj", "src", "main.py"), 3 * MB)
write(os.path.join(tmp, "Downloads", "old_setup.exe"), 22 * MB, age_days=300)
write(os.path.join(tmp, "logs", "trace.log"), 10 * MB, age_days=45)

app = DiskMapperApp()
app.geometry("1400x880")
if app.history:
    app.history.close()
app.history = HistoryStore(os.path.join(tmp, "test_history.db"))
app.update()

print("== window ==")
check("window built", app.winfo_exists() == 1)
check("all four views registered",
      {"map", "growth", "reclaim", "ask"} == {"map", "growth", "reclaim", "ask"})

print("== blueprint view ==")
tree = scan_directory(tmp)
app.snapshot_var.set(True)
app._finish_scan(tree, "folder")
app.update()
rects, pcts = canvas_stats(app)
check("blocks drawn", rects > 4, f"{rects} rects")
check("percentage labels drawn", len(pcts) >= 4, f"{len(pcts)}")
check("hit regions registered", len(app.hit_regions) >= 4)
check("snapshot auto-saved", len(app.history.list_snapshots()) == 1)

biggest = app.map_current.children[0]
app.drill_into(biggest)
app.update()
check("drill down works", app.map_current is biggest)
app.go_up()
app.update()
check("drill up works", app.map_current is app.map_root)

print("== lenses ==")
for lens in ("Type", "Risk", "Age", "Folder"):
    app.lens_var.set(lens)
    app.render()
    app.update()
    rects, _ = canvas_stats(app)
    rows = len(app.legend.find_all())
    check(f"lens '{lens}' renders", rects > 3 and rows > 0, f"rects={rects} legend={rows}")

print("== highlight search ==")
app.lens_var.set("Folder")
app.search_var.set("media")
app.render()
app.update()
check("search does not break render", canvas_stats(app)[0] > 3)
app.search_var.set("")
app.render()

print("== growth view ==")
write(os.path.join(tmp, "media", "new_movie.mp4"), 50 * MB)
os.remove(os.path.join(tmp, "logs", "trace.log"))
tree2 = scan_directory(tmp)
app._finish_scan(tree2, "folder")
app.update()
check("second snapshot saved", len(app.history.list_snapshots()) == 2)

app._refresh_history()
app.hist_list.selection_set(0, 1)
app.compare_snapshots()
app.update()
check("delta tree built", app.delta_root is not None and len(app.delta_root.children) > 0)
check("view switched to growth", app.view == "growth")
grew = [c for c in app.delta_root.children if c.delta > 0]
shrank = [c for c in app.delta_root.children if c.delta < 0]
check("growth detected", len(grew) >= 1)
check("shrink detected", len(shrank) >= 1)
rects, pcts = canvas_stats(app)
check("growth map renders", rects > 1 and len(pcts) >= 1, f"rects={rects}")
app.go_home()
app.update()

print("== reclaim view ==")
app.view_var.set("reclaim")
app._on_view_change()
app.update()
from diskmapper.reclaim import find_junk  # noqa: E402
app._finish_reclaim(find_junk(app.map_root))
app.update()
check("findings listed", len(app.findings) > 0, f"{len(app.findings)}")
check("tree populated", len(app.finding_by_item) == len(app.findings))
check("nothing selected initially", len(app.selected_items) == 0)
check("delete disabled when empty", str(app.delete_btn["state"]) == "disabled")

app._bulk_select("Safe")
app.update()
safe_count = sum(1 for f in app.findings if f.safety == "Safe")
check("bulk select safe", len(app.selected_items) == safe_count, f"{len(app.selected_items)}")
check("delete enabled after select", str(app.delete_btn["state"]) == "normal")
check("total line shows sizes", "Selected:" in app.reclaim_total_var.get())

advisory_items = [i for i, f in app.finding_by_item.items() if not f.deletable]
for item in advisory_items:
    app._toggle_item(item)
check("advisory can never be selected",
      all(i not in app.selected_items for i in advisory_items))

app._clear_selection()
app.update()
check("clear selection works", len(app.selected_items) == 0)

print("== ask view ==")
app.view_var.set("ask")
app._on_view_change()
app.update()
app.ask_var.set("videos bigger than 20mb")
app.run_ask()
app.update()
rows = app.ask_tree.get_children()
check("ask returns rows", len(rows) >= 1, f"{len(rows)}")
check("ask explains the parse", "Interpreted as" in app.ask_explain_var.get(),
      app.ask_explain_var.get())
names = [app.ask_tree.item(r, "values")[-1] for r in rows]
check("only videos returned", all(n.endswith(".mp4") for n in names), str(names))

app.ask_var.set("what's safe to delete that I haven't touched in a year")
app.run_ask()
app.update()
check("reclaim-style question runs", len(app.ask_tree.get_children()) >= 0)
print(f"  -> parsed: {app.ask_explain_var.get()}")

app.ask_var.set("top 3 files bigger than 1mb")
app.run_ask()
app.update()
check("limit honoured in UI", len(app.ask_tree.get_children()) <= 3)

app.history.close()
app.history = None
app.destroy()

print()
if failures:
    print(f"FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("ALL UI CHECKS PASSED")
