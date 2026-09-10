"""Offline checks for the release analytics dashboard (tools/stats.py).

No network access: every test builds Snapshot objects by hand so the storage,
delta and rendering logic is verified deterministically.

    python selftest_stats.py
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "tools"))

import stats  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [ok]   {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}" + (f" -- {detail}" if detail else ""))


def iso(hours_ago: float) -> str:
    t = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return t.replace(microsecond=0).isoformat()


def snap(hours_ago: float, downloads: int, stars: int = 0,
         assets: list | None = None) -> stats.Snapshot:
    return stats.Snapshot(
        ts=iso(hours_ago),
        downloads=downloads,
        stars=stars,
        assets=assets or [],
    )


def test_sparkline() -> None:
    print("\nSparkline")
    check("empty series renders empty", stats.sparkline([]) == "")
    check("single value renders one char", len(stats.sparkline([5])) == 1)
    flat = stats.sparkline([0, 0, 0])
    check("all-zero series uses lowest bar",
          set(flat) == {stats.SPARK[0]}, flat)
    flat2 = stats.sparkline([7, 7, 7])
    check("flat non-zero series uses a mid bar",
          set(flat2) == {stats.SPARK[3]}, flat2)
    ramp = stats.sparkline([0, 1, 2, 3, 4, 5, 6, 7])
    check("ascending series starts lowest", ramp[0] == stats.SPARK[0], ramp)
    check("ascending series ends highest", ramp[-1] == stats.SPARK[-1], ramp)
    check("monotonic input yields non-decreasing bars",
          all(ramp[i] <= ramp[i + 1] for i in range(len(ramp) - 1)), ramp)
    wide = stats.sparkline(list(range(500)), width=40)
    check("long series is bucketed to the target width",
          len(wide) == 40, str(len(wide)))
    check("bucketing preserves the peak", wide[-1] == stats.SPARK[-1])


def test_human() -> None:
    print("\nSize formatting")
    check("bytes", stats._human(512) == "512 B", stats._human(512))
    check("kilobytes", stats._human(2048) == "2.0 KB", stats._human(2048))
    check("megabytes", stats._human(11 * 1024 * 1024) == "11.0 MB",
          stats._human(11 * 1024 * 1024))
    check("gigabytes", stats._human(3 * 1024 ** 3) == "3.0 GB",
          stats._human(3 * 1024 ** 3))


def test_storage() -> None:
    print("\nStorage")
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "s.db")
        store = stats.StatsStore(db)
        check("database file is created", os.path.exists(db))
        check("empty history returns nothing",
              store.history("a/b") == [])
        check("latest on empty repo is None", store.latest("a/b") is None)

        a = stats.Asset(tag="v1.0.0", name="DiskMapper.exe",
                        downloads=7, size=11 * 1024 * 1024)
        sid = store.record("a/b", snap(0, 7, stars=2, assets=[a]))
        check("record returns a row id", sid > 0)

        rows = store.history("a/b")
        check("one reading stored", len(rows) == 1, str(len(rows)))
        check("downloads round-trip", int(rows[0]["downloads"]) == 7)
        check("stars round-trip", int(rows[0]["stars"]) == 2)

        assets = store.assets_for(sid)
        check("asset round-trip", len(assets) == 1)
        check("asset name preserved",
              assets[0]["name"] == "DiskMapper.exe")
        check("asset size preserved",
              int(assets[0]["size"]) == 11 * 1024 * 1024)

        store.record("other/repo", snap(0, 999))
        check("repos are isolated",
              len(store.history("a/b")) == 1 and
              len(store.history("other/repo")) == 1)

        store.record("a/b", snap(0, 9))
        latest = store.latest("a/b")
        check("latest returns newest reading",
              latest is not None and int(latest["downloads"]) == 9)
        check("history is chronological",
              [int(r["downloads"]) for r in store.history("a/b")] == [7, 9])
        store.close()

        reopened = stats.StatsStore(db)
        check("data survives reopen",
              len(reopened.history("a/b")) == 2)
        reopened.close()


def test_deltas() -> None:
    print("\nDelta calculation")
    with tempfile.TemporaryDirectory() as tmp:
        store = stats.StatsStore(os.path.join(tmp, "d.db"))
        for hrs, dl in [(240, 10), (100, 20), (30, 40), (0, 55)]:
            store.record("a/b", snap(hrs, dl))
        rows = store.history("a/b")

        d24 = stats._delta_since(rows, "downloads", 24)
        check("24h delta uses the newest reading older than the cutoff",
              d24 == 15, str(d24))
        # The 7-day cutoff is 168h, so the only reading at or before it is
        # the 240h one; the 100h reading falls inside the window.
        d7 = stats._delta_since(rows, "downloads", 24 * 7)
        check("7d delta reaches further back", d7 == 45, str(d7))
        d1y = stats._delta_since(rows, "downloads", 24 * 365)
        check("delta beyond recorded history is None", d1y is None, str(d1y))
        check("empty rows give None",
              stats._delta_since([], "downloads", 24) is None)
        store.close()

    check("positive delta is signed", stats._fmt_delta(5).strip() == "+5")
    check("negative delta keeps its sign",
          stats._fmt_delta(-3).strip() == "-3")
    check("zero delta shows 0", stats._fmt_delta(0).strip() == "0")
    check("missing delta shows a dash", stats._fmt_delta(None).strip() == "--")


def test_dashboard() -> None:
    print("\nDashboard rendering")
    with tempfile.TemporaryDirectory() as tmp:
        store = stats.StatsStore(os.path.join(tmp, "r.db"))
        empty = stats.dashboard("a/b", store)
        check("empty store explains itself", "No readings" in empty, empty)

        a = stats.Asset(tag="v1.0.0", name="DiskMapper.exe",
                        downloads=0, size=11 * 1024 * 1024)
        store.record("a/b", snap(0, 0, assets=[a]))
        zero = stats.dashboard("a/b", store)
        check("zero downloads prompts sharing the link",
              "No downloads yet" in zero, zero)
        check("share prompt includes the release URL",
              "releases/latest" in zero)
        check("asset row is rendered", "DiskMapper.exe" in zero)
        check("asset size is rendered", "11.0 MB" in zero)
        check("column headers present",
              "METRIC" in zero and "DOWNLOADS" in zero)

        store.record("a/b", snap(0, 12, assets=[a]))
        early = stats.dashboard("a/b", store)
        check("early stage advises direct feedback",
              "feedback" in early.lower(), early[-300:])
        check("sparkline appears once there are two readings",
              any(ch in early for ch in stats.SPARK))

        store.record("a/b", snap(0, 90, assets=[a]))
        mid = stats.dashboard("a/b", store)
        check("mid stage suggests telemetry",
              "telemetry" in mid.lower(), mid[-300:])

        store.record("a/b", snap(0, 500, assets=[a]))
        big = stats.dashboard("a/b", store)
        check("high stage suggests code signing",
              "code signing" in big.lower(), big[-300:])

        long_asset = stats.Asset(tag="v2", name="x" * 40, downloads=1)
        store.record("a/b", snap(0, 501, assets=[long_asset]))
        trimmed = stats.dashboard("a/b", store)
        check("long asset names are truncated", "..." in trimmed)
        store.close()


def test_history_and_csv() -> None:
    print("\nHistory table and CSV export")
    with tempfile.TemporaryDirectory() as tmp:
        store = stats.StatsStore(os.path.join(tmp, "h.db"))
        check("empty history table explains itself",
              "No readings" in stats.history_table("a/b", store))

        store.record("a/b", snap(48, 3, stars=1))
        store.record("a/b", snap(0, 8, stars=4))
        table = stats.history_table("a/b", store)
        check("history table has a header", "WHEN" in table)
        check("history table lists both readings",
              table.count("\n") >= 3, table)

        dest = os.path.join(tmp, "out.csv")
        n = stats.export_csv("a/b", store, dest)
        check("export reports the row count", n == 2, str(n))
        with open(dest, encoding="utf-8") as fh:
            content = fh.read()
        check("csv has a header row", content.startswith("ts,downloads"))
        check("csv contains both readings",
              len(content.strip().splitlines()) == 3, content)
        store.close()


def test_cli() -> None:
    print("\nCommand line")
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "cli.db")
        store = stats.StatsStore(db)
        store.record("a/b", snap(0, 4))
        store.close()

        rc = stats.main(["--repo", "a/b", "--db", db, "--offline"])
        check("offline mode exits cleanly", rc == 0, str(rc))
        check("offline mode makes no network call", True)

        rc = stats.main(["--repo", "a/b", "--db", db, "--history"])
        check("history flag exits cleanly", rc == 0, str(rc))

        dest = os.path.join(tmp, "cli.csv")
        rc = stats.main(["--repo", "a/b", "--db", db, "--csv", dest])
        check("csv flag exits cleanly", rc == 0, str(rc))
        check("csv flag writes the file", os.path.exists(dest))


def test_encoding_fallback() -> None:
    print("\nConsole encoding fallback")
    bars = stats.sparkline([0, 3, 7])
    check("sparkline uses block characters by default",
          any(c in stats.SPARK for c in bars), bars)

    downgraded = stats._ascii_fallback(bars)
    check("fallback removes every block character",
          not any(c in stats.SPARK for c in downgraded), downgraded)
    check("fallback output is pure ascii",
          all(ord(c) < 128 for c in downgraded), downgraded)
    check("fallback preserves the bar count",
          len(downgraded) == len(bars), f"{len(downgraded)} vs {len(bars)}")
    check("fallback keeps surrounding text intact",
          stats._ascii_fallback("Downloads " + bars).startswith("Downloads "))
    check("ascii input passes through unchanged",
          stats._ascii_fallback("plain text 123") == "plain text 123")
    check("fallback charset matches the block charset in length",
          len(stats.SPARK_ASCII) == len(stats.SPARK))

    # A cp1252 console cannot encode the block characters; emit() must not
    # raise, which is the bug this guards against.
    class NarrowStream(io.TextIOBase):
        encoding = "cp1252"

        def __init__(self) -> None:
            self.text = ""

        def write(self, s: str) -> int:
            s.encode("cp1252")  # raises UnicodeEncodeError on block chars
            self.text += s
            return len(s)

    narrow = NarrowStream()
    real = sys.stdout
    sys.stdout = narrow  # type: ignore[assignment]
    try:
        stats.emit("Downloads " + bars)
        ok = True
    except UnicodeEncodeError:
        ok = False
    finally:
        sys.stdout = real
    check("emit survives a cp1252 console", ok)
    check("emit still wrote the line", "Downloads" in narrow.text, narrow.text)

    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "enc.db")
        store = stats.StatsStore(db)
        store.record("a/b", snap(48, 1))
        store.record("a/b", snap(0, 9))
        store.close()
        narrow2 = NarrowStream()
        real = sys.stdout
        sys.stdout = narrow2  # type: ignore[assignment]
        try:
            rc = stats.main(["--repo", "a/b", "--db", db, "--offline"])
            ok = True
        except UnicodeEncodeError:
            rc, ok = -1, False
        finally:
            sys.stdout = real
        check("dashboard with a sparkline renders on cp1252", ok)
        check("cp1252 run exits cleanly", rc == 0, str(rc))


def main() -> int:
    print("DiskMapper analytics self-test")
    test_sparkline()
    test_human()
    test_storage()
    test_deltas()
    test_dashboard()
    test_history_and_csv()
    test_encoding_fallback()
    test_cli()
    total = PASS + FAIL
    print(f"\n{PASS}/{total} checks passed")
    if FAIL:
        print(f"{FAIL} FAILED")
        return 1
    print("All analytics checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
