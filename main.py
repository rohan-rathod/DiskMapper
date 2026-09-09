"""DiskMapper entry point.

Usage:
    python main.py                 # open the GUI
    python main.py --scan C:\\Users  # scan a path immediately on startup
    python main.py --apps          # scan installed applications on startup
"""

from __future__ import annotations

import argparse
import sys

from diskmapper.ui import DiskMapperApp


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="DiskMapper - disk space blueprint")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--scan", metavar="PATH", help="folder or drive to map on startup")
    group.add_argument("--apps", action="store_true",
                       help="map installed applications on startup")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    app = DiskMapperApp()

    if args.apps:
        app.mode_var.set("Installed Applications")
        app._on_mode_change()
        app.after(400, app.start_scan)
    elif args.scan:
        app.mode_var.set("Folder")
        app._on_mode_change()
        app.target_var.set(args.scan)
        app.after(400, app.start_scan)

    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
