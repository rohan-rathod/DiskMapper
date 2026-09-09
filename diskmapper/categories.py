"""File categorisation and safety classification.

Powers the "colour by type", "colour by risk" lenses, the reclaim rules and the
natural-language query engine.
"""

from __future__ import annotations

import os
import time
from typing import Dict, Tuple

# -- Type lens -------------------------------------------------------------

CATEGORY_COLORS: Dict[str, str] = {
    "Video": "#B5652F",
    "Image": "#8C5BB0",
    "Audio": "#A3506B",
    "Archive": "#7A8B33",
    "Installer": "#B0463F",
    "Disk Image": "#8A3F7A",
    "Document": "#2E86AB",
    "Code": "#1F8A70",
    "Database": "#4C8577",
    "Cache / Temp": "#5A6B7C",
    "System": "#3F6DB5",
    "Other": "#356E9E",
    "Folder": "#1D6FA5",
}

_EXT_MAP: Dict[str, str] = {}


def _register(category: str, extensions: str) -> None:
    for ext in extensions.split():
        _EXT_MAP[ext] = category


_register("Video", ".mp4 .mkv .avi .mov .wmv .flv .webm .m4v .mpg .mpeg .ts .m2ts .vob .rmvb")
_register("Image", ".jpg .jpeg .png .gif .bmp .tiff .tif .webp .heic .raw .cr2 .nef .svg .ico .psd .ai")
_register("Audio", ".mp3 .wav .flac .aac .ogg .wma .m4a .opus .aiff .mid")
_register("Archive", ".zip .rar .7z .tar .gz .bz2 .xz .cab .tgz .zst .lz4 .jar")
_register("Installer", ".exe .msi .msix .appx .msu .deb .rpm .dmg .pkg .apk")
_register("Disk Image", ".iso .vhd .vhdx .vmdk .vdi .img .wim .esd .qcow2")
_register("Document", ".pdf .doc .docx .xls .xlsx .ppt .pptx .txt .rtf .odt .ods .csv .md .epub .mobi .one")
_register("Code", ".py .js .ts .tsx .jsx .java .c .cpp .h .hpp .cs .go .rs .rb .php .swift .kt .sh "
                  ".ps1 .bat .html .css .scss .json .xml .yaml .yml .toml .ipynb .sql .vue .lua .pl .r")
_register("Database", ".db .sqlite .sqlite3 .mdb .accdb .mdf .ldf .bak .dbf .parquet .avro")
_register("Cache / Temp", ".tmp .temp .log .cache .bak .old .dmp .chk .etl .crash .part .partial .~tmp")
_register("System", ".dll .sys .drv .ocx .cpl .winmd .pdb .lib .so .dylib .pyd .node .msp .mum .manifest")

_CACHE_DIR_HINTS = (
    "cache", "caches", "temp", "tmp", "__pycache__", "node_modules",
    ".gradle", ".m2", ".nuget", "logs", "crashdumps", "webcache",
)


def categorize(name: str, is_dir: bool, path: str = "") -> str:
    """Return the display category for a file or folder."""
    if is_dir:
        lowered = name.lower()
        if any(hint == lowered or hint in lowered for hint in _CACHE_DIR_HINTS):
            return "Cache / Temp"
        return "Folder"
    ext = os.path.splitext(name)[1].lower()
    return _EXT_MAP.get(ext, "Other")


def category_color(category: str) -> str:
    return CATEGORY_COLORS.get(category, CATEGORY_COLORS["Other"])


# -- Risk lens -------------------------------------------------------------

RISK_LEVELS = ("Reclaimable", "App Data", "User Data", "System Critical")

RISK_COLORS = {
    "Reclaimable": "#2E9E5B",     # green - safe to remove
    "App Data": "#C89B2A",        # amber - app will rebuild or reinstall
    "User Data": "#2E86AB",       # blue - yours, keep
    "System Critical": "#B0463F", # red - do not touch
}

_SYSTEM_MARKERS = (
    "\\windows\\system32", "\\windows\\syswow64", "\\windows\\winsxs",
    "\\windows\\servicing", "\\windows\\boot", "\\system volume information",
    "\\$recycle.bin\\s-1-5-18", "\\windows\\assembly", "\\windows\\fonts",
    "\\recovery", "\\bootmgr", "\\pagefile.sys", "\\hiberfil.sys", "\\swapfile.sys",
)

_RECLAIM_MARKERS = (
    "\\temp\\", "\\tmp\\", "\\__pycache__", "\\node_modules", "\\.pytest_cache",
    "\\.mypy_cache", "\\pip\\cache", "\\npm-cache", "\\yarn\\cache",
    "\\softwaredistribution\\download", "\\$recycle.bin", "\\crashdumps",
    "\\windows\\logs", "\\inetcache", "\\thumbnails", "\\webcache",
    "\\code cache", "\\gpucache", "\\.gradle\\caches", "\\dxcache",
)

_USER_MARKERS = (
    "\\desktop\\", "\\documents\\", "\\pictures\\", "\\videos\\", "\\music\\",
    "\\downloads\\", "\\onedrive\\", "\\dropbox\\", "\\google drive\\", "\\source\\repos\\",
)


def classify_risk(path: str, name: str = "", is_dir: bool = False) -> str:
    """Heuristic safety label used by the risk lens and the reclaim engine."""
    lowered = (path or name).lower()
    padded = lowered if lowered.endswith("\\") else lowered + "\\"

    for marker in _SYSTEM_MARKERS:
        if marker in padded:
            return "System Critical"
    for marker in _RECLAIM_MARKERS:
        if marker in padded:
            return "Reclaimable"

    category = categorize(name or os.path.basename(path), is_dir, path)
    if category == "Cache / Temp":
        return "Reclaimable"
    for marker in _USER_MARKERS:
        if marker in padded:
            return "User Data"
    if "\\appdata\\" in padded or "\\programdata\\" in padded:
        return "App Data"
    if "\\program files" in padded or "\\windows\\" in padded:
        return "System Critical"
    return "User Data"


def risk_color(level: str) -> str:
    return RISK_COLORS.get(level, "#356E9E")


# -- Age lens --------------------------------------------------------------

AGE_BUCKETS: Tuple[Tuple[str, float, str], ...] = (
    ("Today",        1,    "#59E3FF"),
    ("This week",    7,    "#2E9E5B"),
    ("This month",   31,   "#7A8B33"),
    ("This year",    365,  "#C89B2A"),
    ("1-2 years",    730,  "#B5652F"),
    ("Older",        1e9,  "#8C3B3B"),
)


def age_bucket(mtime: float, now: float = 0.0) -> Tuple[str, str]:
    """Return (label, colour) for a modification timestamp."""
    if not mtime:
        return "Unknown", "#5A6B7C"
    now = now or time.time()
    days = max((now - mtime) / 86400.0, 0.0)
    for label, limit, colour in AGE_BUCKETS:
        if days <= limit:
            return label, colour
    return "Older", "#8C3B3B"


def days_old(mtime: float, now: float = 0.0) -> float:
    if not mtime:
        return 0.0
    return max(((now or time.time()) - mtime) / 86400.0, 0.0)
