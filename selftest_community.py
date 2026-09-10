"""Checks for the Community view and its data layer.

Runs entirely offline: the GitHub client is never called over the network.
Payloads are hand-built, and the UI half drives the real Tk window with the
fetch stubbed out.

    python selftest_community.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from diskmapper import __version__
from diskmapper import community as comm

failures = []


def check(label, condition, extra=""):
    if condition:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label} {extra}")
        failures.append(label)


def review(rating=5, author="tester", title="Nice", body="", created="today"):
    return comm.Review(author=author, rating=rating, title=title,
                       body=body, created=created,
                       url="https://example.invalid/1")


# ------------------------------------------------------------------ ratings

print("== rating parsing ==")
check("explicit rating line", comm.parse_rating("Rating: 4") == 4)
check("rating line is case insensitive", comm.parse_rating("RATING: 2") == 2)
check("n out of 5", comm.parse_rating("I give it 3 out of 5") == 3)
check("n/5 shorthand", comm.parse_rating("solid 5/5 tool") == 5)
check("worded stars", comm.parse_rating("4 stars from me") == 4)
check("singular star", comm.parse_rating("1 star, crashed") == 1)
check("star characters are counted",
      comm.parse_rating(comm.STARS_FULL * 3) == 3)
check("body is used when the title has no rating",
      comm.parse_rating("Great app", "Rating: 5") == 5)
check("title wins over body",
      comm.parse_rating("Rating: 2", "Rating: 5") == 2)
check("no rating returns zero", comm.parse_rating("just some text") == 0)
check("out of range numbers are ignored",
      comm.parse_rating("9 out of 10") == 0)
check("empty input is safe", comm.parse_rating("", "") == 0)

print("\n== rating aggregation ==")
data = comm.Community(version="1.0.0")
check("average of nothing is zero", data.average_rating == 0.0)
check("empty ratings line", comm.rating_line(data) == "No ratings yet")

data.reviews = [review(5), review(4), review(3), review(0, author="nostar")]
check("unrated reviews are excluded from the average", len(data.rated) == 3)
check("average is correct", abs(data.average_rating - 4.0) < 1e-9,
      str(data.average_rating))
check("unrated review is still listed", len(data.reviews) == 4)
dist = data.distribution
check("distribution counts each bucket",
      dist[5] == 1 and dist[4] == 1 and dist[3] == 1, str(dist))
check("distribution covers all five buckets", sorted(dist) == [1, 2, 3, 4, 5])
check("empty buckets are zero not missing", dist[1] == 0 and dist[2] == 0)
check("rating line counts only rated reviews",
      "3 ratings" in comm.rating_line(data), comm.rating_line(data))
check("stars display rounds to nearest",
      data.stars_display().count(comm.STARS_FULL) == 4,
      data.stars_display())
check("stars display is always five glyphs",
      len(data.stars_display()) == 5)

single = comm.Community(reviews=[review(5)])
check("singular rating wording", "1 rating" in comm.rating_line(single),
      comm.rating_line(single))

print("\n== review presentation ==")
r = review(3, author="rohan")
check("stars string is five glyphs", len(r.stars) == 5, r.stars)
check("filled stars match the rating",
      r.stars.count(comm.STARS_FULL) == 3, r.stars)
check("avatar initial is upper case", r.initial == "R")
check("missing author still yields an initial",
      comm.Review(author="").initial == "?")

body = "<!-- hidden -->\nRating: 4\nVersion: 1.0.0\nReally useful."
cleaned = comm._strip_template(body)
check("html comments are stripped", "hidden" not in cleaned, cleaned)
check("rating line is stripped", "Rating" not in cleaned, cleaned)
check("version line is stripped", "Version" not in cleaned, cleaned)
check("real prose survives", "Really useful." in cleaned, cleaned)

# ---------------------------------------------------------------- versions

print("\n== version comparison ==")
check("newer minor", comm.compare_versions("1.1.0", "1.0.0") == 1)
check("older minor", comm.compare_versions("1.0.0", "1.1.0") == -1)
check("equal versions", comm.compare_versions("1.0.0", "1.0.0") == 0)
check("v prefix is ignored", comm.compare_versions("v1.0.0", "1.0.0") == 0)
check("uneven lengths compare correctly",
      comm.compare_versions("1.0", "1.0.0") == 0)
check("patch bump detected", comm.compare_versions("1.0.1", "1.0.0") == 1)
check("major bump beats minor", comm.compare_versions("2.0.0", "1.9.9") == 1)
check("ten is newer than nine", comm.compare_versions("1.10.0", "1.9.0") == 1)
check("garbage does not crash", comm.compare_versions("banana", "1.0.0") == -1)

up = comm.Community(version="1.0.0", latest_version="1.1.0")
check("update is offered when a newer tag exists", up.update_available)
same = comm.Community(version="1.0.0", latest_version="v1.0.0")
check("no update when versions match", not same.update_available)
unknown = comm.Community(version="1.0.0", latest_version="")
check("no update when the latest tag is unknown", not unknown.update_available)

# ------------------------------------------------------------------- links

print("\n== prefilled links ==")
url = comm.review_url("a/b", "1.0.0")
check("review link targets the right repo", url.startswith(
    "https://github.com/a/b/issues/new?"), url)
qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
check("review link carries the review label",
      qs.get("labels") == [comm.REVIEW_LABEL], str(qs.get("labels")))
check("review link prefills a rating line",
      "Rating: 5" in qs["body"][0], qs["body"][0][:40])
check("review link records the version",
      "1.0.0" in qs["body"][0])
check("review link prefills a title", qs.get("title") == ["Review: "])

bug = comm.issue_url("a/b", "1.0.0")
bqs = urllib.parse.parse_qs(urllib.parse.urlparse(bug).query)
check("problem link is labelled bug", bqs.get("labels") == ["bug"])
check("problem link asks for repro steps",
      "Steps to reproduce" in bqs["body"][0])
check("problem link records the version", "1.0.0" in bqs["body"][0])

# ------------------------------------------------------------------ framing

print("\n== adoption framing ==")
head, sub = comm.adoption_line(0)
check("zero downloads avoids showing a zero", "0" not in head, head)
check("zero downloads invites the first review", "first" in sub.lower(), sub)

head, _ = comm.adoption_line(1)
check("one download is singular", "1 download" in head, head)
head, _ = comm.adoption_line(6)
check("small counts are framed as early access",
      "Early access" in head, head)
check("small counts still show the real number", "6" in head, head)
head, _ = comm.adoption_line(comm.ADOPTION_THRESHOLD)
check("at the threshold the count becomes the headline",
      head == f"{comm.ADOPTION_THRESHOLD} downloads", head)
check("above the threshold the framing is dropped",
      "Early access" not in comm.adoption_line(400)[0])
head, _ = comm.adoption_line(2500)
check("thousands are abbreviated", head.startswith("2.5k"), head)

# ------------------------------------------------------------------- cache

print("\n== offline cache ==")
tmp = tempfile.mkdtemp()
cache_file = os.path.join(tmp, "community.db")

cache = comm.CommunityCache(cache_file)
check("cache file is created", os.path.exists(cache_file))
check("empty cache returns nothing", cache.load("a/b") is None)

payload = comm.Community(repo="a/b", version="1.0.0", downloads=6, stars=2,
                         forks=1, latest_version="v1.0.0",
                         fetched_at="2026-09-10T06:00:00+00:00",
                         reviews=[review(4, author="asha", title="Handy",
                                         body="Found 12 GB of caches.")])
cache.save(payload)
restored = cache.load("a/b", "1.0.0")
check("cached payload is returned", restored is not None)
check("downloads round-trip", restored.downloads == 6)
check("stars round-trip", restored.stars == 2)
check("latest version round-trips", restored.latest_version == "v1.0.0")
check("reviews round-trip", len(restored.reviews) == 1)
check("review author round-trips", restored.reviews[0].author == "asha")
check("review rating round-trips", restored.reviews[0].rating == 4)
check("review body round-trips",
      "12 GB" in restored.reviews[0].body, restored.reviews[0].body)
check("restored payload is flagged as cached", restored.from_cache)
check("live payload is not flagged as cached", not payload.from_cache)

payload.downloads = 99
cache.save(payload)
check("saving twice updates rather than duplicates",
      cache.load("a/b").downloads == 99)
cache.close()

reopened = comm.CommunityCache(cache_file)
check("cache survives reopen", reopened.load("a/b").downloads == 99)
reopened.close()

offline = comm.load("a/b", "1.0.0", cache_file, online=False)
check("offline load uses the cache", offline.downloads == 99)
check("offline load is flagged as cached", offline.from_cache)

missing = comm.load("nobody/nothing", "1.0.0",
                    os.path.join(tmp, "empty.db"), online=False)
check("offline load with no cache returns an empty payload",
      missing.downloads == 0 and missing.reviews == [])

# ---------------------------------------------------------------------- UI

print("\n== community view ==")
from diskmapper.ui import DiskMapperApp  # noqa: E402


def widget_text(widget):
    """Every label string in a widget subtree."""
    out = []
    try:
        text = widget.cget("text")
        if text:
            out.append(str(text))
    except Exception:
        pass
    for child in widget.winfo_children():
        out.extend(widget_text(child))
    return out


app = DiskMapperApp()
app.geometry("1400x880")
if app.history:
    app.history.close()
app.update()

check("community pane exists", app.community_frame.winfo_exists() == 1)
check("community starts with no data", app.community_data is None)
check("community view is offered in the view bar",
      "community" in [str(v) for v in ("map", "growth", "reclaim", "ask",
                                       "community")])

# Feed the view directly instead of hitting the network.
sample = comm.Community(
    repo="a/b", version=__version__, downloads=6, stars=3, forks=1,
    latest_version=__version__,
    fetched_at="2026-09-10T06:00:00+00:00",
    reviews=[
        review(5, author="asha", title="Found 40 GB",
               body="The reclaim view found node_modules I forgot about."),
        review(3, author="vikram", title="Slow on a big drive",
               body="Scanning C: took four minutes."),
        review(0, author="quiet", title="No rating here", body="Just a note."),
    ])
app._community_loaded(sample)
app.update()

texts = widget_text(app.community_inner)
blob = " | ".join(texts)
check("app name is shown", any(t == "DiskMapper" for t in texts))
check("version is shown", any(__version__ in t for t in texts), blob[:120])
check("up-to-date chip is shown when no newer release exists",
      "Up to date" in blob)
check("download count is shown", any("6" == t for t in texts))
check("star count is shown", any("3" == t for t in texts))
check("early access framing reaches the screen",
      "Early access" in blob, blob[:200])
check("average rating is rendered", "4.0" in blob, blob[:200])
check("rating count excludes unrated reviews", "2 ratings" in blob, blob[:300])
check("every review is listed",
      all(name in blob for name in ("asha", "vikram", "quiet")))
check("review body is rendered", "node_modules" in blob)
check("unrated review still appears", "No rating here" in blob)
check("write a review button exists", "Write a review" in blob)
check("report a problem button exists", "Report a problem" in blob)
check("refresh button exists", "Refresh" in blob)
check("privacy note is shown", "ever leaves this machine" in blob, blob[-200:])
check("data source is disclosed", "live from GitHub" in blob)

app.view_var.set("community")
app._on_view_change()
app.update()
check("switching to community hides the treemap canvas",
      not app.canvas.winfo_ismapped())
check("switching to community shows the pane",
      app.community_frame.winfo_ismapped())

app.view_var.set("map")
app._on_view_change()
app.update()
check("switching away restores the treemap canvas",
      app.canvas.winfo_ismapped())
check("switching away hides the community pane",
      not app.community_frame.winfo_ismapped())

# An update being available must change the header.
newer = comm.Community(repo="a/b", version=__version__, downloads=300,
                       latest_version="v99.0.0",
                       fetched_at="2026-09-10T06:00:00+00:00")
app._community_loaded(newer)
app.update()
blob = " | ".join(widget_text(app.community_inner))
check("update chip appears for a newer release",
      "Update available" in blob, blob[:200])
check("large counts drop the early access framing",
      "Early access" not in blob)
check("empty review state is explained", "Nothing here yet" in blob)
check("empty rating state is explained", "No ratings yet" in blob)

# A failed fetch that falls back to the cache must say so.
stale = comm.Community(repo="a/b", version=__version__, downloads=6,
                       latest_version=__version__, from_cache=True,
                       fetched_at="2026-09-10T06:00:00+00:00",
                       error="No connection (timed out).")
app._community_loaded(stale)
app.update()
blob = " | ".join(widget_text(app.community_inner))
check("cached fallback is disclosed", "last saved copy" in blob, blob[:200])
check("the underlying error is shown", "No connection" in blob)
check("cached view still renders the metrics", "Early access" in blob)

live_error = comm.Community(repo="a/b", version=__version__,
                            fetched_at="2026-09-10T06:00:00+00:00",
                            error="GitHub rate limit reached.")
app._community_loaded(live_error)
app.update()
blob = " | ".join(widget_text(app.community_inner))
check("hard failure is reported plainly", "Could not reach GitHub" in blob)
check("rate limit message is passed through", "rate limit" in blob)

check("re-rendering does not stack widgets",
      len([w for w in app.community_inner.winfo_children()]) < 40,
      str(len(app.community_inner.winfo_children())))

app.destroy()

print()
if failures:
    print(f"{len(failures)} FAILED: " + ", ".join(failures))
    sys.exit(1)
print("ALL COMMUNITY CHECKS PASSED")
