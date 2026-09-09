# DiskMapper — Disk Space Blueprint

A desktop app that draws your PC's storage as a **blueprint floor plan**, then goes
four steps further than every other disk visualiser: it remembers what your disk
looked like last week, tells you what's safe to delete, answers questions in plain
English, and recolours the whole map by type, risk or age.

![style](https://img.shields.io/badge/UI-Blueprint-59E3FF)
![python](https://img.shields.io/badge/python-3.8%2B-blue)
![deps](https://img.shields.io/badge/dependencies-none-success)
![tests](https://img.shields.io/badge/tests-93%20passing-brightgreen)

---

## Quick start

Double-click **`run.bat`**, or:

```powershell
cd "$env:USERPROFILE\Desktop\DiskMapper"
python main.py
```

Zero third-party packages — standard library only.

---

## The four views

Switch with the **VIEW** row at the top.

### 1. Blueprint — where your space actually went
A squarified treemap: every folder, file or installed app is a rectangle whose
**area is proportional to its size**, labelled with size and **percentage**.
Sub-folders are drawn nested inside their parent, three levels deep, like rooms
inside a building plan. Click to zoom in, right-click for a context menu.

### 2. Growth — *what no other tool shows you*
Every scan is saved as a snapshot. Pick any two in the **History** tab and press
**Compare** to get a **delta map**: block size is *how many bytes changed*,
**red = grew, green = shrank**.

This answers the question disk tools never could: *"my drive lost 40 GB this
month — to what?"* One glance and you know.

### 3. Reclaim — space you can actually get back
Scans your tree against a rule engine and reports what's safe to remove:

| Rule | Examples |
|------|----------|
| Build artefacts | `node_modules`, `__pycache__`, `target`, `dist`, `.next` |
| Package caches | `.gradle`, `.m2`, `.nuget`, `.cargo`, browser caches |
| Temp files | `%TEMP%`, crash dumps, `.log`, `.dmp`, `.etl` |
| Old installers | `.exe`/`.msi`/`.iso` sitting in Downloads for 90+ days |
| Windows Update | `SoftwareDistribution` download cache |
| Stale large files | 100 MB+ untouched for six months |
| **Duplicates** | Byte-identical files found by content hash (opt-in) |

Every finding is graded **Safe / Review / Advisory** and explains *why* it's listed.

### 4. Ask — natural language, fully offline
Type a question, get answers. No API key, no network, nothing leaves your machine:

```
videos bigger than 500mb
what's safe to delete that I haven't touched in a year
top 20 installers in downloads older than 6 months
code files modified in the last 7 days
folders bigger than 2gb
```

It shows you exactly how it interpreted your question, so you always know what
you're looking at.

---

## Lenses — recolour the entire map

| Lens | What the colours mean |
|------|----------------------|
| **Folder** | One colour per top-level folder (classic) |
| **Type** | Video, Image, Audio, Archive, Installer, Code, Database, Cache… |
| **Risk** | 🟢 Reclaimable · 🟡 App Data · 🔵 User Data · 🔴 System Critical |
| **Age** | Today → this week → this month → this year → 1–2 years → older |

The **Age** lens instantly reveals cold data you've forgotten about. The **Risk**
lens shows at a glance how much of your disk you're actually allowed to touch.

---

## Safety — this app deletes files, so it is paranoid about it

- **Recycle Bin only.** Nothing is ever permanently deleted. Everything stays
  restorable from Explorer.
- **Hard-blocked paths.** `System32`, `WinSxS`, `WindowsApps`, `servicing`,
  `Boot`, `Recovery` and friends are refused at the API level — even if something
  else asks for them.
- **Advisory items can't be selected.** Virtual disks (`.vhdx`, `.vmdk`) are
  reported so you can see them, but the checkbox is disabled: those must be
  shrunk from Docker/WSL/VMware, never deleted.
- **Explicit confirmation** showing item count and bytes before anything moves.
- **Symlinks and junctions are never followed**, so no infinite loops and no
  double counting.

---

## Everything else

- Drive / Folder / **Installed Applications** (read from the Windows uninstall registry)
- Background scanning — the UI never freezes; **Stop** or `Esc` cancels instantly
- Hover readout: size, percentage, type, risk and age for any block
- `Ctrl+F` highlight — matching blocks glow amber, everything else dims
- Right-click any block: Open in Explorer, Copy path, Move to Recycle Bin
- CSV export of the map, the reclaim list, or your query results
- Free-space readout for the scanned volume
- Access-denied folders are marked, not crashed on

## Keyboard

| Key | Action |
|-----|--------|
| `F5` | Scan |
| `Esc` | Cancel |
| `Backspace` | Up one level |
| `Ctrl+F` | Focus highlight box |

## Command line

```powershell
python main.py                            # GUI
python main.py --scan "C:\Program Files"  # scan a path on startup
python main.py --apps                     # map installed applications
```

---

## Build a shareable .exe

Users shouldn't need Python. Build a single self-contained executable:

```powershell
pip install pyinstaller     # one-time
.\build.bat
```

`build.bat` regenerates the icon, runs the self-tests, and produces
**`dist\DiskMapper.exe`** (~11 MB). That one file is the whole app — copy it
anywhere and double-click. No Python, no install, no dependencies.

Want a proper installer with a Start Menu entry and uninstaller? Install
[Inno Setup 6](https://jrsoftware.org/isdl.php) and run:

```powershell
iscc installer\DiskMapper.iss
```

That produces `installer\Output\DiskMapper-Setup-1.0.0.exe`.

> **Before sharing widely:** unsigned executables trigger a SmartScreen warning
> ("Windows protected your PC"), which most users won't click past. An OV/EV
> code-signing certificate removes it and is the single highest-value spend for
> public distribution.

---

## Project layout

```
DiskMapper/
├── main.py                 entry point + CLI flags
├── run.bat                 double-click launcher (source)
├── build.bat               builds dist\DiskMapper.exe
├── selftest.py             21 core checks
├── selftest_features.py    39 engine checks
├── selftest_ui.py          33 GUI checks
├── assets/diskmapper.ico   generated app icon
├── tools/
│   ├── make_icon.py        icon generator (stdlib only, no Pillow)
│   └── version_info.txt    Windows version resource
├── installer/
│   └── DiskMapper.iss      Inno Setup installer script
└── diskmapper/
    ├── model.py            node tree, size/mtime roll-up, formatting
    ├── treemap.py          squarified treemap layout
    ├── scanner.py          filesystem walker + registry reader + cancellation
    ├── categories.py       type / risk / age classification
    ├── storage.py          SQLite scan history + delta engine
    ├── reclaim.py          junk rules, duplicate hashing, safe deleter
    ├── query.py            natural-language → structured query compiler
    └── ui.py               blueprint canvas, four views, lenses, export
```

Scan history lives in `%LOCALAPPDATA%\DiskMapper\history.db`.

## Verify

```powershell
python selftest.py ; python selftest_features.py ; python selftest_ui.py
```

93 checks covering layout maths (area conservation, bounds, proportionality),
size roll-up, cancellation, classification, query parsing, junk rules, duplicate
detection, delete guard rails, snapshot diffing, and every UI view.

---

## Notes

- Whole-drive scans touch hundreds of thousands of files; the first `C:\` run
  takes a few minutes. Progress shows in the status bar. Run as Administrator
  for complete coverage.
- Installed-app sizes come from each program's registry `EstimatedSize`; when a
  program doesn't publish one, its install folder is measured directly.
- Sizes are logical file sizes (Explorer's "Size"), not size-on-disk.
- Growth comparison needs **two scans of the same target** — scan once today,
  once next week.
