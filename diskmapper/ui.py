"""DiskMapper UI - blueprint treemap with growth, reclaim and ask views."""

from __future__ import annotations

import csv
import os
import queue
import subprocess
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional, Tuple

from . import query as nlq
from .categories import (AGE_BUCKETS, age_bucket, categorize, category_color,
                         classify_risk, days_old, risk_color)
from .model import Node, human_size
from .reclaim import (Finding, find_duplicates, find_junk, group_findings,
                      send_to_recycle_bin)
from .scanner import (CancelledError, ScanCancel, drive_usage, list_drives,
                      scan_directory, scan_installed_apps)
from .storage import HistoryStore, build_delta_tree, summarize_delta
from .treemap import squarify

# ---- Blueprint palette ---------------------------------------------------
BG_DEEP = "#071B33"
BG_PAPER = "#0B2A4A"
GRID_LINE = "#123B63"
GRID_LINE_MAJOR = "#17507F"
INK = "#DCF3FF"
INK_DIM = "#7FA8C9"
ACCENT = "#59E3FF"
PANEL = "#0A2340"
WARN = "#FFB454"
GOOD = "#3FD68C"
BAD = "#FF6B6B"

BLOCK_COLORS = [
    "#1D6FA5", "#1F8A70", "#8C5BB0", "#B5652F", "#2E86AB",
    "#4C8577", "#A3506B", "#3F6DB5", "#7A8B33", "#B0463F",
    "#356E9E", "#5D8233",
]

MAX_DRAW_DEPTH = 3
MIN_BLOCK_PX = 3.0
LENSES = ("Folder", "Type", "Risk", "Age")


def _blend(hex_color: str, target: str, factor: float) -> str:
    factor = max(0.0, min(1.0, factor))
    c1 = tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    c2 = tuple(int(target[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02X%02X%02X" % tuple(round(a + (b - a) * factor) for a, b in zip(c1, c2))


def _reveal(path: str) -> None:
    """Open the file's folder in Explorer with the item selected."""
    if not path or not os.path.exists(path):
        return
    try:
        if os.path.isdir(path):
            os.startfile(path)  # noqa: S606
        else:
            subprocess.Popen(["explorer", f"/select,{os.path.normpath(path)}"])
    except OSError:
        pass


class DiskMapperApp(tk.Tk):
    """Main application window."""

    def __init__(self) -> None:
        super().__init__()
        self.title("DiskMapper - Disk Space Blueprint")
        self.geometry("1400x880")
        self.minsize(1080, 680)
        self.configure(bg=BG_DEEP)

        # scan state
        self.map_root: Optional[Node] = None
        self.map_current: Optional[Node] = None
        self.delta_root: Optional[Node] = None
        self.delta_current: Optional[Node] = None
        self.scan_mode = "folder"

        # view state
        self.view = "map"
        self.hit_regions: List[Tuple[float, float, float, float, Node, int]] = []
        self.hover_node: Optional[Node] = None
        self.color_index: Dict[int, int] = {}
        self._lens_cache: Dict[Tuple[int, str], List[Tuple[str, str, int]]] = {}

        # reclaim state
        self.findings: List[Finding] = []
        self.finding_by_item: Dict[str, Finding] = {}
        self.selected_items: set = set()

        # ask state
        self.ask_results: List[Node] = []

        self.cancel = ScanCancel()
        self.result_queue: "queue.Queue" = queue.Queue()
        self.worker: Optional[threading.Thread] = None

        try:
            self.history = HistoryStore()
        except Exception:
            self.history = None

        self._build_styles()
        self._build_toolbar()
        self._build_viewbar()
        self._build_body()
        self._build_statusbar()
        self._bind_events()

        self.after(120, self._poll_queue)
        self._refresh_history()
        self._show_placeholder()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ==================================================================
    # construction
    # ==================================================================
    def _build_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Blue.TFrame", background=BG_DEEP)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("Blue.TLabel", background=BG_DEEP, foreground=INK)
        style.configure("Panel.TLabel", background=PANEL, foreground=INK)
        style.configure("Dim.TLabel", background=PANEL, foreground=INK_DIM)
        style.configure("DimDeep.TLabel", background=BG_DEEP, foreground=INK_DIM)
        style.configure("Title.TLabel", background=BG_DEEP, foreground=ACCENT,
                        font=("Consolas", 13, "bold"))
        style.configure("Blue.TButton", background="#14406B", foreground=INK,
                        borderwidth=0, focuscolor=BG_DEEP, padding=(10, 5),
                        font=("Segoe UI", 9))
        style.map("Blue.TButton",
                  background=[("active", "#1D5C96"), ("disabled", "#0E2C4A")],
                  foreground=[("disabled", "#4E6E8A")])
        style.configure("Go.TButton", background="#1E7A5A", foreground="#EAFFF6",
                        borderwidth=0, padding=(10, 5), font=("Segoe UI", 9, "bold"))
        style.map("Go.TButton", background=[("active", "#28A177"), ("disabled", "#143A2E")])
        style.configure("Danger.TButton", background="#8A3030", foreground="#FFECEC",
                        borderwidth=0, padding=(10, 5), font=("Segoe UI", 9, "bold"))
        style.map("Danger.TButton", background=[("active", "#B04040"), ("disabled", "#3A1E1E")])
        style.configure("Blue.TCombobox", fieldbackground="#14406B",
                        background="#14406B", foreground=INK, arrowcolor=INK)
        style.configure("Blue.Horizontal.TProgressbar", background=ACCENT,
                        troughcolor="#0E2C4A", borderwidth=0)
        style.configure("Blue.TCheckbutton", background=BG_DEEP, foreground=INK,
                        focuscolor=BG_DEEP)
        style.map("Blue.TCheckbutton", background=[("active", BG_DEEP)])
        style.configure("Blue.TRadiobutton", background=BG_DEEP, foreground=INK,
                        focuscolor=BG_DEEP)
        style.map("Blue.TRadiobutton", background=[("active", BG_DEEP)])
        style.configure("Blue.TNotebook", background=PANEL, borderwidth=0)
        style.configure("Blue.TNotebook.Tab", background="#0E2C4A", foreground=INK_DIM,
                        padding=(12, 5), font=("Segoe UI", 9))
        style.map("Blue.TNotebook.Tab",
                  background=[("selected", "#1D5C96")], foreground=[("selected", INK)])
        style.configure("Blue.Treeview", background=BG_PAPER, fieldbackground=BG_PAPER,
                        foreground=INK, borderwidth=0, rowheight=22,
                        font=("Consolas", 9))
        style.configure("Blue.Treeview.Heading", background="#14406B", foreground=INK,
                        borderwidth=0, font=("Segoe UI", 9, "bold"))
        style.map("Blue.Treeview", background=[("selected", "#1D5C96")])
        style.configure("Blue.TEntry", fieldbackground="#14406B", foreground=INK,
                        insertcolor=ACCENT, borderwidth=0)

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, style="Blue.TFrame", padding=(12, 9, 12, 4))
        bar.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(bar, text="DISK  BLUEPRINT", style="Title.TLabel").pack(side=tk.LEFT, padx=(0, 14))

        self.mode_var = tk.StringVar(value="Drive")
        mode = ttk.Combobox(bar, textvariable=self.mode_var, width=17, state="readonly",
                            values=("Drive", "Folder", "Installed Applications"),
                            style="Blue.TCombobox")
        mode.pack(side=tk.LEFT, padx=(0, 6))
        mode.bind("<<ComboboxSelected>>", lambda _e: self._on_mode_change())

        self.target_var = tk.StringVar()
        drives = list_drives()
        self.target_box = ttk.Combobox(bar, textvariable=self.target_var, width=40,
                                       values=drives, style="Blue.TCombobox")
        if drives:
            self.target_var.set(drives[0])
        self.target_box.pack(side=tk.LEFT, padx=(0, 6))

        self.browse_btn = ttk.Button(bar, text="Browse...", style="Blue.TButton",
                                     command=self._browse)
        self.browse_btn.pack(side=tk.LEFT, padx=(0, 6))
        self.scan_btn = ttk.Button(bar, text="Scan", style="Go.TButton", command=self.start_scan)
        self.scan_btn.pack(side=tk.LEFT, padx=(0, 6))
        self.cancel_btn = ttk.Button(bar, text="Stop", style="Blue.TButton",
                                     command=self.cancel_work, state=tk.DISABLED)
        self.cancel_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.snapshot_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Save to history", variable=self.snapshot_var,
                        style="Blue.TCheckbutton").pack(side=tk.LEFT, padx=(6, 0))

        ttk.Button(bar, text="Export CSV", style="Blue.TButton",
                   command=self.export_csv).pack(side=tk.RIGHT)
        ttk.Button(bar, text="Up", style="Blue.TButton",
                   command=self.go_up).pack(side=tk.RIGHT, padx=(0, 6))
        ttk.Button(bar, text="Home", style="Blue.TButton",
                   command=self.go_home).pack(side=tk.RIGHT, padx=(0, 6))

    def _build_viewbar(self) -> None:
        bar = ttk.Frame(self, style="Blue.TFrame", padding=(12, 2, 12, 6))
        bar.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(bar, text="VIEW", style="DimDeep.TLabel",
                  font=("Consolas", 9, "bold")).pack(side=tk.LEFT, padx=(0, 8))
        self.view_var = tk.StringVar(value="map")
        for label, value in (("Blueprint", "map"), ("Growth", "growth"),
                             ("Reclaim", "reclaim"), ("Ask", "ask")):
            ttk.Radiobutton(bar, text=label, value=value, variable=self.view_var,
                            style="Blue.TRadiobutton",
                            command=self._on_view_change).pack(side=tk.LEFT, padx=(0, 10))

        ttk.Label(bar, text="   LENS", style="DimDeep.TLabel",
                  font=("Consolas", 9, "bold")).pack(side=tk.LEFT, padx=(10, 8))
        self.lens_var = tk.StringVar(value="Folder")
        lens = ttk.Combobox(bar, textvariable=self.lens_var, width=10, state="readonly",
                            values=LENSES, style="Blue.TCombobox")
        lens.pack(side=tk.LEFT)
        lens.bind("<<ComboboxSelected>>", lambda _e: self.render())

        ttk.Label(bar, text="   HIGHLIGHT", style="DimDeep.TLabel",
                  font=("Consolas", 9, "bold")).pack(side=tk.LEFT, padx=(10, 8))
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(bar, textvariable=self.search_var, width=24,
                                      style="Blue.TEntry")
        self.search_entry.pack(side=tk.LEFT)
        self.search_var.trace_add("write", lambda *_a: self.render())

    def _build_body(self) -> None:
        crumb = ttk.Frame(self, style="Blue.TFrame", padding=(14, 0, 14, 6))
        crumb.pack(side=tk.TOP, fill=tk.X)
        self.crumb_var = tk.StringVar(value="No scan yet")
        ttk.Label(crumb, textvariable=self.crumb_var, style="Blue.TLabel",
                  font=("Consolas", 10)).pack(side=tk.LEFT)

        body = ttk.Frame(self, style="Blue.TFrame", padding=(12, 0, 12, 8))
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.stage = ttk.Frame(body, style="Blue.TFrame")
        self.stage.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(self.stage, bg=BG_PAPER, highlightthickness=1,
                                highlightbackground=GRID_LINE_MAJOR, bd=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self._build_reclaim_pane()
        self._build_ask_pane()
        self._build_sidebar(body)

    def _build_reclaim_pane(self) -> None:
        self.reclaim_frame = ttk.Frame(self.stage, style="Blue.TFrame")

        top = ttk.Frame(self.reclaim_frame, style="Blue.TFrame", padding=(0, 0, 0, 6))
        top.pack(fill=tk.X)
        ttk.Button(top, text="Analyse current scan", style="Go.TButton",
                   command=self.run_reclaim).pack(side=tk.LEFT)
        self.dupes_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Also hunt duplicates (slower)", variable=self.dupes_var,
                        style="Blue.TCheckbutton").pack(side=tk.LEFT, padx=(10, 0))
        ttk.Button(top, text="Select all safe", style="Blue.TButton",
                   command=lambda: self._bulk_select("Safe")).pack(side=tk.LEFT, padx=(16, 6))
        ttk.Button(top, text="Clear selection", style="Blue.TButton",
                   command=self._clear_selection).pack(side=tk.LEFT)

        columns = ("size", "safety", "reason")
        self.reclaim_tree = ttk.Treeview(self.reclaim_frame, columns=columns,
                                         style="Blue.Treeview", selectmode="none")
        self.reclaim_tree.heading("#0", text="   Item")
        self.reclaim_tree.heading("size", text="Size")
        self.reclaim_tree.heading("safety", text="Safety")
        self.reclaim_tree.heading("reason", text="Why it is here")
        self.reclaim_tree.column("#0", width=430, stretch=True)
        self.reclaim_tree.column("size", width=100, anchor="e", stretch=False)
        self.reclaim_tree.column("safety", width=90, anchor="center", stretch=False)
        self.reclaim_tree.column("reason", width=420, stretch=True)
        self.reclaim_tree.tag_configure("group", foreground=ACCENT, font=("Consolas", 9, "bold"))
        self.reclaim_tree.tag_configure("safe", foreground=GOOD)
        self.reclaim_tree.tag_configure("review", foreground=WARN)
        self.reclaim_tree.tag_configure("advisory", foreground=BAD)
        self.reclaim_tree.pack(fill=tk.BOTH, expand=True)
        self.reclaim_tree.bind("<Button-1>", self._on_reclaim_click)
        self.reclaim_tree.bind("<Double-1>", self._on_reclaim_open)

        bottom = ttk.Frame(self.reclaim_frame, style="Blue.TFrame", padding=(0, 8, 0, 0))
        bottom.pack(fill=tk.X)
        self.reclaim_total_var = tk.StringVar(value="Run an analysis to see what you can get back.")
        ttk.Label(bottom, textvariable=self.reclaim_total_var, style="Blue.TLabel",
                  font=("Consolas", 10, "bold")).pack(side=tk.LEFT)
        self.delete_btn = ttk.Button(bottom, text="Move selected to Recycle Bin",
                                     style="Danger.TButton", command=self.delete_selected,
                                     state=tk.DISABLED)
        self.delete_btn.pack(side=tk.RIGHT)

    def _build_ask_pane(self) -> None:
        self.ask_frame = ttk.Frame(self.stage, style="Blue.TFrame")

        top = ttk.Frame(self.ask_frame, style="Blue.TFrame", padding=(0, 0, 0, 6))
        top.pack(fill=tk.X)
        ttk.Label(top, text="Ask in plain English:", style="Blue.TLabel").pack(side=tk.LEFT)
        self.ask_var = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.ask_var, style="Blue.TEntry", width=64)
        entry.pack(side=tk.LEFT, padx=(8, 6))
        entry.bind("<Return>", lambda _e: self.run_ask())
        self.ask_entry = entry
        ttk.Button(top, text="Ask", style="Go.TButton", command=self.run_ask).pack(side=tk.LEFT)

        chips = ttk.Frame(self.ask_frame, style="Blue.TFrame", padding=(0, 0, 0, 6))
        chips.pack(fill=tk.X)
        for suggestion in nlq.SUGGESTIONS[:4]:
            ttk.Button(chips, text=suggestion, style="Blue.TButton",
                       command=lambda s=suggestion: self._use_suggestion(s)).pack(side=tk.LEFT, padx=(0, 6))
        chips2 = ttk.Frame(self.ask_frame, style="Blue.TFrame", padding=(0, 0, 0, 6))
        chips2.pack(fill=tk.X)
        for suggestion in nlq.SUGGESTIONS[4:]:
            ttk.Button(chips2, text=suggestion, style="Blue.TButton",
                       command=lambda s=suggestion: self._use_suggestion(s)).pack(side=tk.LEFT, padx=(0, 6))

        self.ask_explain_var = tk.StringVar(value="")
        ttk.Label(self.ask_frame, textvariable=self.ask_explain_var, style="DimDeep.TLabel",
                  font=("Consolas", 9)).pack(anchor="w", pady=(0, 4))

        columns = ("size", "pct", "kind", "age", "path")
        self.ask_tree = ttk.Treeview(self.ask_frame, columns=columns, style="Blue.Treeview",
                                     show="headings")
        for key, text, width, anchor in (("size", "Size", 100, "e"),
                                         ("pct", "% of scan", 80, "e"),
                                         ("kind", "Type", 110, "w"),
                                         ("age", "Last changed", 120, "w"),
                                         ("path", "Path", 640, "w")):
            self.ask_tree.heading(key, text=text)
            self.ask_tree.column(key, width=width, anchor=anchor,
                                 stretch=(key == "path"))
        self.ask_tree.pack(fill=tk.BOTH, expand=True)
        self.ask_tree.bind("<Double-1>", self._on_ask_open)

        hint = ttk.Label(self.ask_frame,
                         text="Double-click a row to reveal it in Explorer. "
                              "Everything runs locally - nothing is uploaded.",
                         style="DimDeep.TLabel", font=("Segoe UI", 8))
        hint.pack(anchor="w", pady=(6, 0))

    def _build_sidebar(self, parent) -> None:
        self.sidebar = ttk.Notebook(parent, style="Blue.TNotebook", width=340)
        self.sidebar.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
        self.sidebar.pack_propagate(False)

        legend_tab = ttk.Frame(self.sidebar, style="Panel.TFrame", padding=10)
        self.sidebar.add(legend_tab, text="Legend")
        self.summary_var = tk.StringVar(value="-")
        ttk.Label(legend_tab, textvariable=self.summary_var, style="Dim.TLabel",
                  font=("Consolas", 9), justify=tk.LEFT, wraplength=300).pack(anchor="w", pady=(0, 8))
        self.legend = tk.Canvas(legend_tab, bg=PANEL, highlightthickness=0)
        self.legend.pack(fill=tk.BOTH, expand=True)
        self.legend.bind("<Button-1>", self._on_legend_click)

        hist_tab = ttk.Frame(self.sidebar, style="Panel.TFrame", padding=10)
        self.sidebar.add(hist_tab, text="History")
        ttk.Label(hist_tab, text="Pick two scans, then Compare:", style="Panel.TLabel",
                  font=("Segoe UI", 9)).pack(anchor="w")
        self.hist_list = tk.Listbox(hist_tab, bg=BG_PAPER, fg=INK, selectbackground="#1D5C96",
                                    selectforeground=INK, highlightthickness=0, bd=0,
                                    font=("Consolas", 9), selectmode=tk.EXTENDED, height=18,
                                    exportselection=False)
        self.hist_list.pack(fill=tk.BOTH, expand=True, pady=(6, 6))
        row = ttk.Frame(hist_tab, style="Panel.TFrame")
        row.pack(fill=tk.X)
        ttk.Button(row, text="Compare", style="Go.TButton",
                   command=self.compare_snapshots).pack(side=tk.LEFT)
        ttk.Button(row, text="Delete", style="Blue.TButton",
                   command=self.delete_snapshot).pack(side=tk.LEFT, padx=(6, 0))
        self.hist_note_var = tk.StringVar(value="")
        ttk.Label(hist_tab, textvariable=self.hist_note_var, style="Dim.TLabel",
                  font=("Consolas", 8), wraplength=300, justify=tk.LEFT).pack(anchor="w", pady=(8, 0))

    def _build_statusbar(self) -> None:
        bar = ttk.Frame(self, style="Blue.TFrame", padding=(14, 6))
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_var = tk.StringVar(value="Ready. Choose a drive or folder and press Scan (F5).")
        ttk.Label(bar, textvariable=self.status_var, style="Blue.TLabel",
                  font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=180,
                                        style="Blue.Horizontal.TProgressbar")
        self.progress.pack(side=tk.RIGHT)

    def _bind_events(self) -> None:
        self.canvas.bind("<Configure>", lambda _e: self.render())
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Button-3>", self._on_right_click)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", lambda _e: self._set_hover(None))
        self.bind("<BackSpace>", lambda _e: self.go_up())
        self.bind("<F5>", lambda _e: self.start_scan())
        self.bind("<Escape>", lambda _e: self.cancel_work())
        self.bind("<Control-f>", lambda _e: self.search_entry.focus_set())

        self.ctx_menu = tk.Menu(self, tearoff=0, bg=PANEL, fg=INK,
                                activebackground="#1D5C96", activeforeground=INK)
        self.ctx_menu.add_command(label="Open in Explorer", command=self._ctx_reveal)
        self.ctx_menu.add_command(label="Copy path", command=self._ctx_copy)
        self.ctx_menu.add_separator()
        self.ctx_menu.add_command(label="Move to Recycle Bin", command=self._ctx_recycle)
        self.ctx_menu.add_separator()
        self.ctx_menu.add_command(label="Go up one level", command=self.go_up)
        self._ctx_node: Optional[Node] = None

    # ==================================================================
    # view switching
    # ==================================================================
    def _on_view_change(self) -> None:
        self.view = self.view_var.get()
        for widget in (self.canvas, self.reclaim_frame, self.ask_frame):
            widget.pack_forget()
        if self.view in ("map", "growth"):
            self.canvas.pack(fill=tk.BOTH, expand=True)
            if self.view == "growth" and self.delta_root is None:
                self.sidebar.select(1)
                self.status_var.set("Growth view: scan twice, then pick two snapshots "
                                    "in History and press Compare.")
            self.render()
        elif self.view == "reclaim":
            self.reclaim_frame.pack(fill=tk.BOTH, expand=True)
        else:
            self.ask_frame.pack(fill=tk.BOTH, expand=True)
            self.ask_entry.focus_set()

    @property
    def current(self) -> Optional[Node]:
        return self.delta_current if self.view == "growth" else self.map_current

    def _set_current(self, node: Optional[Node]) -> None:
        if self.view == "growth":
            self.delta_current = node
        else:
            self.map_current = node

    # ==================================================================
    # scanning
    # ==================================================================
    def _on_mode_change(self) -> None:
        mode = self.mode_var.get()
        if mode == "Drive":
            drives = list_drives()
            self.target_box.configure(values=drives, state="normal")
            self.browse_btn.configure(state=tk.NORMAL)
            if drives:
                self.target_var.set(drives[0])
        elif mode == "Folder":
            self.target_box.configure(state="normal")
            self.browse_btn.configure(state=tk.NORMAL)
        else:
            self.target_box.configure(state="disabled")
            self.browse_btn.configure(state=tk.DISABLED)
            self.target_var.set("(registry: all installed programs)")

    def _browse(self) -> None:
        path = filedialog.askdirectory(title="Select a folder or drive to map")
        if path:
            self.target_var.set(os.path.normpath(path))
            self.mode_var.set("Folder")

    def _busy(self) -> bool:
        return bool(self.worker and self.worker.is_alive())

    def start_scan(self) -> None:
        if self._busy():
            messagebox.showinfo("DiskMapper", "A job is already running.")
            return
        mode = self.mode_var.get()
        target = self.target_var.get().strip()
        if mode != "Installed Applications" and not os.path.isdir(target):
            messagebox.showerror("DiskMapper", f"Not a valid folder:\n{target}")
            return

        self._start_busy("Scanning...")

        def progress(path: str, dirs: int, files: int) -> None:
            self.result_queue.put(("progress", (path, dirs, files)))

        def work() -> None:
            try:
                if mode == "Installed Applications":
                    node = scan_installed_apps(self.cancel, progress)
                    self.result_queue.put(("scan", (node, "apps")))
                else:
                    node = scan_directory(target, self.cancel, progress)
                    self.result_queue.put(("scan", (node, "folder")))
            except CancelledError:
                self.result_queue.put(("cancelled", None))
            except Exception as exc:
                self.result_queue.put(("error", str(exc)))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def cancel_work(self) -> None:
        if self._busy():
            self.cancel.cancel()
            self.status_var.set("Cancelling...")

    def _start_busy(self, message: str) -> None:
        self.cancel.reset()
        self.scan_btn.configure(state=tk.DISABLED)
        self.cancel_btn.configure(state=tk.NORMAL)
        self.progress.start(14)
        self.status_var.set(message)

    def _end_busy(self, message: str) -> None:
        self.progress.stop()
        self.scan_btn.configure(state=tk.NORMAL)
        self.cancel_btn.configure(state=tk.DISABLED)
        self.status_var.set(message)

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.result_queue.get_nowait()
                if kind == "progress":
                    path, dirs, files = payload
                    shown = path if len(path) < 68 else "..." + path[-65:]
                    self.status_var.set(
                        f"Working  |  {dirs:,} items  {files:,} files  |  {shown}")
                elif kind == "scan":
                    self._finish_scan(*payload)
                elif kind == "reclaim":
                    self._finish_reclaim(payload)
                elif kind == "cancelled":
                    self._end_busy("Cancelled.")
                elif kind == "error":
                    self._end_busy("Job failed.")
                    messagebox.showerror("DiskMapper", f"Failed:\n{payload}")
        except queue.Empty:
            pass
        self.after(120, self._poll_queue)

    def _finish_scan(self, node: Node, mode: str) -> None:
        self.map_root = node
        self.map_current = node
        self.scan_mode = mode
        self.color_index = {id(c): i for i, c in enumerate(node.children)}
        self._lens_cache.clear()
        self.findings = []
        self._render_reclaim()

        summary = f"Scan complete  |  {human_size(node.size)}  |  {len(node.children):,} top-level items"
        if mode == "folder":
            usage = drive_usage(node.path)
            if usage:
                summary += f"  |  {human_size(usage[2])} free on volume"

        if self.snapshot_var.get() and self.history:
            try:
                self.history.save(node, mode=mode)
                summary += "  |  snapshot saved"
                self._refresh_history()
            except Exception:
                pass

        self._end_busy(summary)
        if self.view_var.get() == "growth":
            self.view_var.set("map")
            self._on_view_change()
        else:
            self.render()

    # ==================================================================
    # navigation
    # ==================================================================
    def go_home(self) -> None:
        root = self.delta_root if self.view == "growth" else self.map_root
        if root:
            self._set_current(root)
            self.render()

    def go_up(self) -> None:
        node = self.current
        if node is not None and node.parent is not None:
            self._set_current(node.parent)
            self.render()

    def drill_into(self, node: Node) -> None:
        if node.is_dir and node.children:
            self._set_current(node)
            self.render()
        else:
            self.status_var.set(f"{node.name}  |  {human_size(node.size)}  |  {node.path}")

    def _on_click(self, event: tk.Event) -> None:
        node = self._node_at(event.x, event.y)
        if node:
            self.drill_into(node)

    def _on_right_click(self, event: tk.Event) -> None:
        node = self._node_at(event.x, event.y)
        self._ctx_node = node
        if node and node.path:
            self.ctx_menu.entryconfigure(0, label=f"Open '{node.name[:30]}' in Explorer")
            try:
                self.ctx_menu.tk_popup(event.x_root, event.y_root)
            finally:
                self.ctx_menu.grab_release()
        else:
            self.go_up()

    def _ctx_reveal(self) -> None:
        if self._ctx_node:
            _reveal(self._ctx_node.path)

    def _ctx_copy(self) -> None:
        if self._ctx_node:
            self.clipboard_clear()
            self.clipboard_append(self._ctx_node.path)
            self.status_var.set(f"Copied: {self._ctx_node.path}")

    def _ctx_recycle(self) -> None:
        node = self._ctx_node
        if not node or not node.path or not os.path.exists(node.path):
            return
        if not messagebox.askyesno(
                "Move to Recycle Bin",
                f"Move this to the Recycle Bin?\n\n{node.path}\n{human_size(node.size)}\n\n"
                "It stays restorable from Explorer."):
            return
        moved, errors = send_to_recycle_bin([node.path])
        if moved:
            self.status_var.set(f"Recycled {node.name}. Re-scan to refresh the map.")
        else:
            messagebox.showwarning("DiskMapper", "Could not delete:\n" + "\n".join(errors))

    def _node_at(self, x: float, y: float) -> Optional[Node]:
        best, best_depth = None, -1
        for x0, y0, x1, y1, node, depth in self.hit_regions:
            if x0 <= x <= x1 and y0 <= y <= y1 and depth > best_depth:
                best, best_depth = node, depth
        return best

    def _on_motion(self, event: tk.Event) -> None:
        self._set_hover(self._node_at(event.x, event.y))

    def _set_hover(self, node: Optional[Node]) -> None:
        if node is self.hover_node:
            return
        self.hover_node = node
        self.canvas.delete("tooltip")
        if not node or not self.current:
            return
        pct = node.percent_of(self.current)
        if self.view == "growth":
            sign = "+" if node.delta > 0 else "-"
            text = f"{node.name}\n{sign}{human_size(abs(node.delta))}   ({node.tag})"
        else:
            text = f"{node.name}\n{human_size(node.size)}   {pct:.2f}% of view"
            text += f"\n{categorize(node.name, node.is_dir, node.path)}"
            text += f"  |  {classify_risk(node.path, node.name, node.is_dir)}"
            if node.mtime:
                text += f"  |  {age_bucket(node.mtime)[0].lower()}"
        if node.detail:
            text += f"\n{node.detail}"
        self.canvas.create_text(12, self.canvas.winfo_height() - 12, anchor="sw",
                                text=text, fill=ACCENT, font=("Consolas", 9), tags="tooltip")

    # ==================================================================
    # rendering
    # ==================================================================
    def _show_placeholder(self) -> None:
        self.canvas.delete("all")
        w = self.canvas.winfo_width() or 900
        h = self.canvas.winfo_height() or 600
        self._draw_grid(w, h)
        self.canvas.create_text(w / 2, h / 2 - 26, text="DISK SPACE BLUEPRINT",
                                fill=ACCENT, font=("Consolas", 22, "bold"))
        self.canvas.create_text(w / 2, h / 2 + 8,
                                text="Scan a drive, then use Growth to see what changed, "
                                     "Reclaim to free space, Ask to search in plain English.",
                                fill=INK_DIM, font=("Segoe UI", 10))

    def _draw_grid(self, w: int, h: int) -> None:
        step = 28
        for x in range(0, w, step):
            self.canvas.create_line(x, 0, x, h,
                                    fill=GRID_LINE_MAJOR if x % (step * 4) == 0 else GRID_LINE)
        for y in range(0, h, step):
            self.canvas.create_line(0, y, w, y,
                                    fill=GRID_LINE_MAJOR if y % (step * 4) == 0 else GRID_LINE)

    def render(self) -> None:
        if self.view not in ("map", "growth"):
            return
        node = self.current
        if not node:
            self._show_placeholder()
            self._render_legend()
            return

        self.canvas.delete("all")
        self.hit_regions = []
        w = max(self.canvas.winfo_width(), 50)
        h = max(self.canvas.winfo_height(), 50)
        self._draw_grid(w, h)
        self.crumb_var.set(self._crumb_text(node))

        margin, header = 16, 30
        self.canvas.create_rectangle(margin, margin, w - margin, h - margin,
                                     outline=ACCENT, width=2)
        if self.view == "growth":
            title = f"{node.name}   |   {human_size(node.size)} of change"
        else:
            title = (f"{node.name}   |   {human_size(node.size)}   |   100%"
                     f"   |   lens: {self.lens_var.get()}")
        self.canvas.create_text(margin + 10, margin + 15, anchor="w", text=title,
                                fill=ACCENT, font=("Consolas", 11, "bold"))

        area = (margin + 6, margin + header, w - margin - 6, h - margin - 6)
        if node.size <= 0 or not node.children:
            self.canvas.create_text(w / 2, h / 2, text="Nothing to draw here",
                                    fill=INK_DIM, font=("Segoe UI", 11))
        else:
            self._draw_children(node, area, 0, node.size)
        self._render_legend()

    def _crumb_text(self, node: Node) -> str:
        text = "  >  ".join(n.name for n in node.breadcrumb())
        return text if len(text) <= 150 else "...  >  " + text[-145:]

    def _color_for(self, node: Node, depth: int) -> str:
        if self.view == "growth":
            base = BAD if node.delta > 0 else GOOD
            return _blend(base, BG_PAPER, 0.25 + min(0.1 * depth, 0.4))

        lens = self.lens_var.get()
        if lens == "Type":
            base = category_color(categorize(node.name, node.is_dir, node.path))
        elif lens == "Risk":
            base = risk_color(classify_risk(node.path, node.name, node.is_dir))
        elif lens == "Age":
            base = age_bucket(node.mtime)[1]
        else:
            ancestor = node
            while ancestor.parent is not None and id(ancestor) not in self.color_index:
                ancestor = ancestor.parent
            index = self.color_index.get(id(ancestor), abs(hash(node.name)))
            base = BLOCK_COLORS[index % len(BLOCK_COLORS)]
        return _blend(base, BG_PAPER, min(0.12 * depth, 0.55))

    def _draw_children(self, parent: Node, area, depth: int, root_total: int) -> None:
        if depth > MAX_DRAW_DEPTH or not parent.children:
            return
        x0, y0, x1, y1 = area
        w, h = x1 - x0, y1 - y0
        if w < MIN_BLOCK_PX or h < MIN_BLOCK_PX:
            return

        children = [c for c in parent.children if c.size > 0]
        if not children:
            return
        rects = squarify([c.size for c in children], x0, y0, w, h)
        needle = self.search_var.get().strip().lower()

        for child, (rx, ry, rw, rh) in zip(children, rects):
            if rw < MIN_BLOCK_PX or rh < MIN_BLOCK_PX:
                continue
            hit = bool(needle) and (needle in child.name.lower() or needle in child.path.lower())
            fill = self._color_for(child, depth)
            if needle and not hit:
                fill = _blend(fill, BG_PAPER, 0.72)
            outline = WARN if hit else (ACCENT if depth == 0 else _blend(ACCENT, BG_PAPER, 0.45))
            width = 3 if hit else (2 if depth == 0 else 1)
            self.canvas.create_rectangle(rx, ry, rx + rw, ry + rh,
                                         fill=fill, outline=outline, width=width)
            self.hit_regions.append((rx, ry, rx + rw, ry + rh, child, depth))

            pct = child.size * 100.0 / root_total if root_total else 0.0
            self._label_block(child, rx, ry, rw, rh, pct)

            if child.is_dir and child.children and rw > 46 and rh > 46:
                pad = 6 if depth == 0 else 4
                head = 18 if (depth == 0 and rh > 60) else 3
                inner = (rx + pad, ry + head + pad * 0.5, rx + rw - pad, ry + rh - pad)
                if inner[2] - inner[0] > MIN_BLOCK_PX and inner[3] - inner[1] > MIN_BLOCK_PX:
                    self._draw_children(child, inner, depth + 1, root_total)

    def _label_block(self, node: Node, x: float, y: float, w: float, h: float,
                     pct: float) -> None:
        if w < 46 or h < 20:
            return
        pct_text = f"{pct:.1f}%" if pct >= 0.1 else "<0.1%"
        if self.view == "growth":
            sign = "+" if node.delta > 0 else "-"
            size_text = f"{sign}{human_size(abs(node.delta))}   {pct_text}"
        else:
            size_text = f"{human_size(node.size)}   {pct_text}"

        if h >= 46 and w >= 96:
            self.canvas.create_text(x + 6, y + 5, anchor="nw",
                                    text=self._fit(node.name, w, 7.0),
                                    fill=INK, font=("Consolas", 9, "bold"))
            self.canvas.create_text(x + 6, y + 20, anchor="nw", text=size_text,
                                    fill=_blend(INK, BG_PAPER, 0.25), font=("Consolas", 8))
        else:
            self.canvas.create_text(x + w / 2, y + h / 2, text=pct_text,
                                    fill=INK, font=("Consolas", 8))

    @staticmethod
    def _fit(text: str, width: float, char_px: float) -> str:
        limit = max(int((width - 12) / char_px), 3)
        return text if len(text) <= limit else text[:limit - 1] + "\u2026"

    # -- legend ----------------------------------------------------------
    def _lens_breakdown(self, node: Node) -> List[Tuple[str, str, int]]:
        """Aggregate the current subtree by the active lens (cached)."""
        lens = self.lens_var.get()
        key = (id(node), lens)
        if key in self._lens_cache:
            return self._lens_cache[key]

        totals: Dict[str, int] = {}
        colors: Dict[str, str] = {}
        now = time.time()
        for item in node.walk():
            if item is node or item.is_dir:
                continue
            if lens == "Type":
                label = categorize(item.name, False, item.path)
                colors[label] = category_color(label)
            elif lens == "Risk":
                label = classify_risk(item.path, item.name, False)
                colors[label] = risk_color(label)
            else:
                label, colour = age_bucket(item.mtime, now)
                colors[label] = colour
            totals[label] = totals.get(label, 0) + item.size

        rows = [(label, colors[label], size) for label, size in totals.items()]
        rows.sort(key=lambda r: r[2], reverse=True)
        if len(self._lens_cache) > 200:
            self._lens_cache.clear()
        self._lens_cache[key] = rows
        return rows

    def _render_legend(self) -> None:
        self.legend.delete("all")
        node = self.current
        if not node:
            self.summary_var.set("-")
            return

        lens = self.lens_var.get()
        if self.view == "growth":
            grew = sum(c.delta for c in node.children if c.delta > 0)
            shrank = -sum(c.delta for c in node.children if c.delta < 0)
            self.summary_var.set(f"{node.name}\n+{human_size(grew)} added\n"
                                 f"-{human_size(shrank)} removed\n"
                                 f"net {human_size(grew - shrank)}")
        else:
            self.summary_var.set(f"{node.name}\n{human_size(node.size)}  |  "
                                 f"{len(node.children):,} items  |  {node.file_count:,} files")

        rows: List[Tuple[str, str, int, Optional[Node]]] = []
        if self.view == "map" and lens != "Folder":
            rows = [(label, colour, size, None) for label, colour, size in
                    self._lens_breakdown(node)[:14]]
            self.legend.create_text(0, 0, anchor="nw", text=f"BY {lens.upper()}",
                                    fill=ACCENT, font=("Consolas", 9, "bold"))
        else:
            rows = [(c.name, self._color_for(c, 0), c.size, c) for c in node.children[:14]]
            self.legend.create_text(0, 0, anchor="nw", text="TOP ITEMS",
                                    fill=ACCENT, font=("Consolas", 9, "bold"))

        self._legend_nodes = [row[3] for row in rows]
        base = 18
        for i, (label, colour, size, _node) in enumerate(rows):
            top = base + i * 30
            self.legend.create_rectangle(0, top, 14, top + 14, fill=colour, outline=ACCENT)
            self.legend.create_text(22, top - 2, anchor="nw", text=self._fit(label, 250, 6.6),
                                    fill=INK, font=("Consolas", 9))
            pct = size * 100.0 / node.size if node.size else 0.0
            self.legend.create_text(22, top + 12, anchor="nw",
                                    text=f"{human_size(size)}   {pct:5.1f}%",
                                    fill=INK_DIM, font=("Consolas", 8))
            bar = max(2.0, min(pct, 100.0) * 2.9)
            self.legend.create_rectangle(200, top + 13, 200 + bar, top + 19,
                                         fill=ACCENT, outline="")
        if len(node.children) > 14 and (self.view != "map" or lens == "Folder"):
            self.legend.create_text(0, base + 14 * 30 + 4, anchor="nw",
                                    text=f"+ {len(node.children) - 14:,} more items",
                                    fill=WARN, font=("Consolas", 9))

    def _on_legend_click(self, event: tk.Event) -> None:
        index = int((event.y - 18) // 30)
        nodes = getattr(self, "_legend_nodes", [])
        if 0 <= index < len(nodes) and nodes[index] is not None:
            self.drill_into(nodes[index])

    # ==================================================================
    # growth / history
    # ==================================================================
    def _refresh_history(self) -> None:
        if not self.history:
            return
        self.hist_list.delete(0, tk.END)
        try:
            self.snapshots = self.history.list_snapshots(limit=60)
        except Exception:
            self.snapshots = []
        for snap in self.snapshots:
            when = time.strftime("%d %b %H:%M", time.localtime(snap["taken_at"]))
            root = snap["root"]
            short = root if len(root) <= 18 else "..." + root[-15:]
            self.hist_list.insert(
                tk.END, f"{when}  {human_size(snap['total_size']):>10}  {short}")
        self.hist_note_var.set(f"{len(self.snapshots)} snapshot(s) stored.\n"
                               f"{self.history.db_path}")

    def compare_snapshots(self) -> None:
        if not self.history:
            return
        picks = list(self.hist_list.curselection())
        if len(picks) != 2:
            if len(self.snapshots) >= 2:
                picks = [0, 1]
            else:
                messagebox.showinfo("DiskMapper",
                                    "Select exactly two snapshots to compare "
                                    "(scan the same folder twice to build history).")
                return
        newer, older = sorted(picks)
        snap_new, snap_old = self.snapshots[newer], self.snapshots[older]
        if snap_new["root"] != snap_old["root"]:
            messagebox.showwarning("DiskMapper",
                                   "Those snapshots cover different folders. "
                                   "Pick two scans of the same target.")
            return
        if snap_new["taken_at"] < snap_old["taken_at"]:
            snap_new, snap_old = snap_old, snap_new

        try:
            old_entries = self.history.entries(snap_old["id"])
            new_entries = self.history.entries(snap_new["id"])
        except Exception as exc:
            messagebox.showerror("DiskMapper", f"Could not read history:\n{exc}")
            return

        delta = build_delta_tree(old_entries, new_entries, snap_new["root"])
        if not delta.children:
            messagebox.showinfo("DiskMapper", "Nothing changed between those two scans.")
            return

        self.delta_root = delta
        self.delta_current = delta
        self.color_index.update({id(c): i for i, c in enumerate(delta.children)})
        self.view_var.set("growth")
        self._on_view_change()
        self.status_var.set(
            f"Growth: {snap_old['label']} -> {snap_new['label']}  |  "
            f"{summarize_delta(snap_old['total_size'], snap_new['total_size'])}  |  "
            f"red = grew, green = shrank")

    def delete_snapshot(self) -> None:
        if not self.history:
            return
        picks = list(self.hist_list.curselection())
        if not picks:
            return
        for index in picks:
            try:
                self.history.delete_snapshot(self.snapshots[index]["id"])
            except Exception:
                pass
        self._refresh_history()

    # ==================================================================
    # reclaim
    # ==================================================================
    def run_reclaim(self) -> None:
        if self._busy():
            messagebox.showinfo("DiskMapper", "A job is already running.")
            return
        if not self.map_root:
            messagebox.showinfo("DiskMapper", "Scan a drive or folder first.")
            return
        if self.scan_mode == "apps":
            messagebox.showinfo("DiskMapper",
                                "Reclaim works on a Drive or Folder scan. "
                                "Scan a real path first.")
            return

        root = self.map_root
        want_dupes = self.dupes_var.get()
        self._start_busy("Analysing reclaimable space...")

        def progress(name: str, done: int, found: int) -> None:
            self.result_queue.put(("progress", (name, done, found)))

        def work() -> None:
            try:
                findings = find_junk(root, self.cancel)
                if want_dupes:
                    findings += find_duplicates(root, self.cancel, progress=progress)
                self.result_queue.put(("reclaim", findings))
            except CancelledError:
                self.result_queue.put(("cancelled", None))
            except Exception as exc:
                self.result_queue.put(("error", str(exc)))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _finish_reclaim(self, findings: List[Finding]) -> None:
        self.findings = findings
        self._render_reclaim()
        total = sum(f.size for f in findings if f.deletable)
        self._end_busy(f"Analysis complete: {human_size(total)} reclaimable "
                       f"across {len(findings)} items.")
        self.view_var.set("reclaim")
        self._on_view_change()

    def _render_reclaim(self) -> None:
        self.reclaim_tree.delete(*self.reclaim_tree.get_children())
        self.finding_by_item.clear()
        self.selected_items.clear()

        if not self.findings:
            self.reclaim_total_var.set("Run an analysis to see what you can get back.")
            self.delete_btn.configure(state=tk.DISABLED)
            return

        for rule, total, items in group_findings(self.findings):
            group = self.reclaim_tree.insert(
                "", tk.END, text=f"  {rule}  ({len(items)} items)",
                values=(human_size(total), "", ""), open=(rule != "Stale large files"),
                tags=("group",))
            for finding in items:
                tag = {"Safe": "safe", "Review": "review"}.get(finding.safety, "advisory")
                extra = ("   [" + ", ".join(finding.extra) + "]") if finding.extra else ""
                item = self.reclaim_tree.insert(
                    group, tk.END,
                    text=f"  {'[ ]' if finding.deletable else ' - '}  {finding.path}",
                    values=(human_size(finding.size), finding.safety,
                            finding.reason + extra),
                    tags=(tag,))
                self.finding_by_item[item] = finding

        self._update_reclaim_total()

    def _on_reclaim_click(self, event: tk.Event) -> None:
        item = self.reclaim_tree.identify_row(event.y)
        if not item:
            return
        if item in self.finding_by_item:
            self._toggle_item(item)
        else:
            children = self.reclaim_tree.get_children(item)
            selectable = [c for c in children if self.finding_by_item[c].deletable]
            turn_on = any(c not in self.selected_items for c in selectable)
            for child in selectable:
                self._set_item_selected(child, turn_on)
        self._update_reclaim_total()

    def _toggle_item(self, item: str) -> None:
        finding = self.finding_by_item[item]
        if not finding.deletable:
            self.status_var.set(f"{finding.name}: advisory only - {finding.reason}")
            return
        self._set_item_selected(item, item not in self.selected_items)

    def _set_item_selected(self, item: str, state: bool) -> None:
        finding = self.finding_by_item[item]
        if not finding.deletable:
            return
        if state:
            self.selected_items.add(item)
        else:
            self.selected_items.discard(item)
        self.reclaim_tree.item(item, text=f"  {'[x]' if state else '[ ]'}  {finding.path}")

    def _bulk_select(self, safety: str) -> None:
        for item, finding in self.finding_by_item.items():
            if finding.safety == safety:
                self._set_item_selected(item, True)
        self._update_reclaim_total()

    def _clear_selection(self) -> None:
        for item in list(self.selected_items):
            self._set_item_selected(item, False)
        self._update_reclaim_total()

    def _update_reclaim_total(self) -> None:
        available = sum(f.size for f in self.findings if f.deletable)
        chosen = sum(self.finding_by_item[i].size for i in self.selected_items)
        advisory = sum(f.size for f in self.findings if not f.deletable)
        text = (f"Reclaimable: {human_size(available)}    |    "
                f"Selected: {human_size(chosen)} ({len(self.selected_items)} items)")
        if advisory:
            text += f"    |    Advisory (not deletable): {human_size(advisory)}"
        self.reclaim_total_var.set(text)
        self.delete_btn.configure(state=tk.NORMAL if self.selected_items else tk.DISABLED)

    def _on_reclaim_open(self, event: tk.Event) -> None:
        item = self.reclaim_tree.identify_row(event.y)
        if item in self.finding_by_item:
            _reveal(self.finding_by_item[item].path)

    def delete_selected(self) -> None:
        if not self.selected_items:
            return
        findings = [self.finding_by_item[i] for i in self.selected_items]
        total = sum(f.size for f in findings)
        risky = [f for f in findings if f.safety == "Review"]

        message = (f"Move {len(findings)} item(s) to the Recycle Bin?\n\n"
                   f"Frees about {human_size(total)}.\n")
        if risky:
            message += (f"\n{len(risky)} item(s) are marked 'Review' - re-check them "
                        f"if you are unsure.\n")
        message += ("\nNothing is permanently deleted: everything goes to the "
                    "Recycle Bin and can be restored from Explorer.")
        if not messagebox.askyesno("Confirm reclaim", message):
            return

        moved, errors = send_to_recycle_bin([f.path for f in findings])
        freed = human_size(total) if moved == len(findings) else "some"
        self.findings = [f for f in self.findings
                         if f.path not in {x.path for x in findings} or os.path.exists(f.path)]
        self._render_reclaim()
        if errors:
            messagebox.showwarning(
                "DiskMapper",
                f"Recycled {moved} of {len(findings)} items.\n\nCould not move:\n"
                + "\n".join(errors[:12]))
        self.status_var.set(f"Recycled {moved} item(s), freeing about {freed}. "
                            f"Re-scan to refresh the blueprint.")

    # ==================================================================
    # ask
    # ==================================================================
    def _use_suggestion(self, text: str) -> None:
        self.ask_var.set(text)
        self.run_ask()

    def run_ask(self) -> None:
        if not self.map_root:
            messagebox.showinfo("DiskMapper", "Scan a drive or folder first.")
            return
        text = self.ask_var.get().strip()
        if not text:
            return

        results, parsed = nlq.ask(self.map_root, text)
        self.ask_results = results
        self.ask_tree.delete(*self.ask_tree.get_children())

        total = sum(n.size for n in results)
        self.ask_explain_var.set(
            f"Interpreted as:  {parsed.describe()}      ->  {len(results)} match(es), "
            f"{human_size(total)}")

        now = time.time()
        scan_total = self.map_root.size or 1
        for node in results:
            age = (f"{days_old(node.mtime, now):.0f} days ago" if node.mtime else "unknown")
            self.ask_tree.insert("", tk.END, values=(
                human_size(node.size),
                f"{node.size * 100.0 / scan_total:.2f}%",
                categorize(node.name, node.is_dir, node.path),
                age,
                node.path))
        self.status_var.set(f"Ask: {len(results)} result(s), {human_size(total)} total.")

    def _on_ask_open(self, event: tk.Event) -> None:
        item = self.ask_tree.identify_row(event.y)
        if not item:
            return
        values = self.ask_tree.item(item, "values")
        if values:
            _reveal(values[-1])

    # ==================================================================
    # export / shutdown
    # ==================================================================
    def export_csv(self) -> None:
        node = self.current
        if self.view == "reclaim" and self.findings:
            self._export_findings()
            return
        if self.view == "ask" and self.ask_results:
            self._export_rows(
                ["Name", "Bytes", "Readable", "Type", "Path"],
                [[n.name, n.size, human_size(n.size),
                  categorize(n.name, n.is_dir, n.path), n.path] for n in self.ask_results],
                "diskmap_ask.csv")
            return
        if not node:
            messagebox.showinfo("DiskMapper", "Run a scan first.")
            return
        self._export_rows(
            ["Name", "Bytes", "Readable", "Percent", "Type", "Risk", "Path", "Detail"],
            [[c.name, c.size, human_size(c.size), f"{c.percent_of(node):.2f}",
              categorize(c.name, c.is_dir, c.path),
              classify_risk(c.path, c.name, c.is_dir), c.path, c.detail]
             for c in node.children],
            f"diskmap_{node.name or 'scan'}.csv")

    def _export_findings(self) -> None:
        self._export_rows(
            ["Rule", "Name", "Bytes", "Readable", "Safety", "Reason", "Path"],
            [[f.rule, f.name, f.size, human_size(f.size), f.safety, f.reason, f.path]
             for f in self.findings],
            "diskmap_reclaim.csv")

    def _export_rows(self, header: List[str], rows: List[List], suggested: str) -> None:
        safe_name = "".join(ch for ch in suggested if ch not in '\\/:*?"<>|')
        path = filedialog.asksaveasfilename(title="Export", defaultextension=".csv",
                                            initialfile=safe_name,
                                            filetypes=[("CSV file", "*.csv")])
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow(header)
                writer.writerows(rows)
            self.status_var.set(f"Exported {len(rows):,} rows to {path}")
        except OSError as exc:
            messagebox.showerror("DiskMapper", f"Could not write file:\n{exc}")

    def _on_close(self) -> None:
        self.cancel.cancel()
        if self.history:
            self.history.close()
        self.destroy()


def main() -> None:
    DiskMapperApp().mainloop()
