"""
biz_images.py

Optional add-on for biz_sentiment.py: an image-based mood signal for /biz/.

What it does
    For each post that carries an image, fetch the small preview picture the
    board already serves (about 125 px), turn it into a few numbers, and throw
    the picture away. Nothing but numbers and a label is stored.

    * A "visual fingerprint" (difference hash) is compared with a folder of
      reference memes you collect yourself:
          meme_refs/bullish/   e.g. bull cartoons, green-candle memes
          meme_refs/bearish/   e.g. pink wojak
      A close match gives the post a bullish (+1) or bearish (-1) image label.
    * The share of green and of red pixels is stored as well. It is NOT used
      for the label: a red price chart can mean dumping or "I shorted and
      won". It is kept so the idea can be tested later.

What it never does
    It never saves an image to disk, never republishes one, and never needs
    the post text.

Commands
    python biz_images.py refs                 list the reference memes found
    python biz_images.py test-image FILE      classify one local image file
    python biz_images.py report [DB]          daily image mood, with z-scores
    python biz_images.py export [DB]          write img_daily.json (counts only)

Needs: Pillow  (pip install pillow)
"""

import json
import math
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

try:
    import requests
except ImportError:  # only needed for live fetching, not for tests
    requests = None
from PIL import Image, ImageOps

LANCZOS = getattr(getattr(Image, "Resampling", Image), "LANCZOS")


def _pixels(img):
    """Flat list of pixel values (works across Pillow versions)."""
    f = getattr(img, "get_flattened_data", None)
    return list(f() if f else img.getdata())

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BOARD = "biz"
THUMB_URL = "https://i.4cdn.org/{board}/{tim}s.jpg"
USER_AGENT = "biz-sentiment-research/0.1 (personal, non-commercial)"
REFS_DIR = Path(os.environ.get("BIZ_MEME_REFS", Path(__file__).resolve().parent / "meme_refs"))

HASH_SIZE = 16            # 16 x 16 = 256-bit fingerprint
MATCH_MAX_DIST = 48       # a match needs a distance of at most this (of 256)
MATCH_MARGIN = 8          # and must beat the best opposite-class ref by this much
MAX_PER_POLL = 50         # preview downloads per poll, newest posts first
FETCH_DELAY = 1.1         # seconds between downloads
MAX_AGE_HOURS = 12        # ignore image posts older than this (threads expire)
IMG_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

_REFS = None
_REFS_SIG = None
_REFS_CHECKED = 0.0


def _refs_signature(base):
    """Names, sizes and modification times of the reference pictures."""
    sig = []
    for label in ("bullish", "bearish"):
        d = Path(base) / label
        if not d.is_dir():
            continue
        for f in sorted(d.iterdir()):
            if f.suffix.lower() in IMG_EXTS and not f.name.startswith("."):
                try:
                    st = f.stat()
                    sig.append((label, f.name, st.st_size, int(st.st_mtime)))
                except OSError:
                    pass
    return tuple(sig)


# ---------------------------------------------------------------------------
# Image -> numbers
# ---------------------------------------------------------------------------

def dhash(img, size=HASH_SIZE):
    """Difference hash: survives resizing and recompression, ignores colour."""
    g = img.convert("L").resize((size + 1, size), LANCZOS)
    px = _pixels(g)
    bits = 0
    for r in range(size):
        row = r * (size + 1)
        for c in range(size):
            bits = (bits << 1) | (1 if px[row + c] > px[row + c + 1] else 0)
    return bits


def hamming(a, b):
    return bin(a ^ b).count("1")


def color_fractions(img):
    """Share of clearly green and clearly red pixels (0..1) in a small copy."""
    hsv = img.convert("RGB").resize((32, 32)).convert("HSV")
    green = red = 0
    px = _pixels(hsv)
    for h, s, v in px:
        if s < 100 or v < 80:
            continue
        if 50 <= h <= 120:
            green += 1
        elif h <= 14 or h >= 240:
            red += 1
    n = len(px)
    return green / n, red / n


def load_refs(folder=None, force=False):
    """Return [(label, name, hash)] for every image under bullish/ and bearish/.

    Each reference is hashed twice, as is and mirrored, because memes are
    often posted flipped.
    """
    global _REFS, _REFS_SIG, _REFS_CHECKED
    if folder is None and _REFS is not None and not force:
        # Pick up pictures added or removed while the scraper is running.
        # The folder is looked at most once every 10 seconds.
        now = time.time()
        if now - _REFS_CHECKED < 10:
            return _REFS
        _REFS_CHECKED = now
        if _refs_signature(REFS_DIR) == _REFS_SIG:
            return _REFS
    base = Path(folder) if folder else REFS_DIR
    refs = []
    for label in ("bullish", "bearish"):
        d = base / label
        if not d.is_dir():
            continue
        for f in sorted(d.iterdir()):
            if f.suffix.lower() not in IMG_EXTS or f.name.startswith("."):
                continue
            try:
                with Image.open(f) as im:
                    im.load()
                    refs.append((label, f.stem, dhash(im)))
                    refs.append((label, f.stem, dhash(ImageOps.mirror(im.convert("RGB")))))
            except Exception as e:  # unreadable file: skip it, keep going
                print(f"  biz_images: could not read reference {f.name}: {e}")
    if folder is None:
        _REFS = refs
        _REFS_SIG = _refs_signature(REFS_DIR)
        _REFS_CHECKED = time.time()
    return refs


def classify(img, refs):
    """Return (label, ref_name, distance). label is None when nothing is close enough
    or when the best bullish and bearish matches are too close to call."""
    if not refs:
        return None, None, None
    h = dhash(img)
    best = {"bullish": (999, None), "bearish": (999, None)}
    for label, name, rh in refs:
        d = hamming(h, rh)
        if d < best[label][0]:
            best[label] = (d, name)
    (bd, bn), (rd, rn) = best["bullish"], best["bearish"]
    if bd <= rd:
        win, d, name, other = "bullish", bd, bn, rd
    else:
        win, d, name, other = "bearish", rd, rn, bd
    if d > MATCH_MAX_DIST or (other - d) < MATCH_MARGIN:
        return None, None, d
    return win, name, d


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS post_images (
            post_id INTEGER PRIMARY KEY,
            thread_id INTEGER,
            post_epoch INTEGER,
            post_date TEXT,
            tim INTEGER,
            md5 TEXT,
            status TEXT,          -- pending | done | gone | failed
            img_label TEXT,       -- bullish | bearish | NULL
            img_score REAL,       -- +1 | -1 | NULL
            ref_name TEXT,
            dist INTEGER,
            green_frac REAL,
            red_frac REAL,
            hash TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_post_images_status ON post_images(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_post_images_date ON post_images(post_date)")


def note_post(conn, post, thread_id, now=None):
    """Record that a post has an image (cheap: no download). Never raises."""
    try:
        if "tim" not in post or "no" not in post:
            return
        ensure_table(conn)
        epoch = int(post.get("time", 0) or 0)
        now = now or time.time()
        if epoch and now - epoch > MAX_AGE_HOURS * 3600:
            return
        date = datetime.fromtimestamp(epoch or now, tz=timezone.utc).date().isoformat()
        conn.execute(
            "INSERT OR IGNORE INTO post_images (post_id, thread_id, post_epoch, post_date, tim, md5, status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
            (post["no"], thread_id, epoch, date, post["tim"], post.get("md5")),
        )
    except Exception as e:
        print(f"  biz_images: note_post error: {e}")


def fetch_thumb(tim, board=BOARD):
    """Download the small preview. Returns an Image, or None if it is gone."""
    r = requests.get(THUMB_URL.format(board=board, tim=tim), headers={"User-Agent": USER_AGENT}, timeout=10)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    img = Image.open(BytesIO(r.content))
    img.load()
    return img


def process_pending(conn, limit=MAX_PER_POLL, fetch=None, now=None, delay=FETCH_DELAY):
    """Fetch and score pending images, newest first. Never raises. Returns a count."""
    done = 0
    try:
        ensure_table(conn)
        now = now or time.time()
        conn.execute("UPDATE post_images SET status='gone' WHERE status='pending' AND post_epoch < ?",
                     (now - MAX_AGE_HOURS * 3600,))
        rows = conn.execute(
            "SELECT post_id, tim, md5 FROM post_images WHERE status='pending' "
            "ORDER BY post_epoch DESC LIMIT ?", (limit,)).fetchall()
        refs = load_refs()
        fetch = fetch or fetch_thumb
        for post_id, tim, md5 in rows:
            # The same file posted again needs no new download.
            cached = conn.execute(
                "SELECT img_label, img_score, ref_name, dist, green_frac, red_frac, hash FROM post_images "
                "WHERE md5 = ? AND status='done' LIMIT 1", (md5,)).fetchone() if md5 else None
            if cached:
                conn.execute("UPDATE post_images SET status='done', img_label=?, img_score=?, ref_name=?, "
                             "dist=?, green_frac=?, red_frac=?, hash=? WHERE post_id=?", (*cached, post_id))
                done += 1
                continue
            try:
                img = fetch(tim)
            except Exception as e:
                conn.execute("UPDATE post_images SET status='failed' WHERE post_id=?", (post_id,))
                print(f"  biz_images: fetch failed for {post_id}: {e}")
                time.sleep(delay)
                continue
            if img is None:
                conn.execute("UPDATE post_images SET status='gone' WHERE post_id=?", (post_id,))
            else:
                label, name, dist = classify(img, refs)
                g, r = color_fractions(img)
                score = {"bullish": 1.0, "bearish": -1.0}.get(label)
                conn.execute(
                    "UPDATE post_images SET status='done', img_label=?, img_score=?, ref_name=?, dist=?, "
                    "green_frac=?, red_frac=?, hash=? WHERE post_id=?",
                    (label, score, name, dist, round(g, 4), round(r, 4), format(dhash(img), "064x"), post_id))
                done += 1
            if delay:
                time.sleep(delay)
        conn.commit()
    except Exception as e:
        print(f"  biz_images: process_pending error: {e}")
    return done


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def daily(conn):
    """One row per day: image posts, scored images, bullish, bearish, index, colour means."""
    ensure_table(conn)
    out = []
    for date, n, scored, bull, bear, g, r in conn.execute("""
        SELECT post_date, COUNT(*), SUM(status='done'),
               SUM(img_label='bullish'), SUM(img_label='bearish'),
               AVG(CASE WHEN status='done' THEN green_frac END),
               AVG(CASE WHEN status='done' THEN red_frac END)
        FROM post_images GROUP BY post_date ORDER BY post_date"""):
        bull, bear = bull or 0, bear or 0
        tot = bull + bear
        out.append({"post_date": date, "image_posts": n, "scored": scored or 0,
                    "bullish": bull, "bearish": bear,
                    "image_index": (bull - bear) / tot if tot else None,
                    "avg_green": None if g is None else round(g, 4),
                    "avg_red": None if r is None else round(r, 4)})
    return out


def _mean_std(v):
    n = len(v)
    if n == 0:
        return None, None
    m = sum(v) / n
    if n < 2:
        return m, None
    return m, math.sqrt(sum((x - m) ** 2 for x in v) / (n - 1))


def report(conn, baseline_days=14):
    rows = daily(conn)
    if not rows:
        print("No image data yet. Run the scraper with biz_images.py next to it.")
        return
    print(f"{'date':<11}{'imgs':>6}{'bull':>6}{'bear':>6}{'index':>8}{'z':>7}  flag")
    for i, r in enumerate(rows):
        hist = [x["image_index"] for x in rows[max(0, i - baseline_days):i] if x["image_index"] is not None]
        m, s = _mean_std(hist)
        z = (r["image_index"] - m) / s if (r["image_index"] is not None and s) else None
        flag = "HIGH" if z is not None and z >= 2 else "LOW" if z is not None and z <= -2 else ""
        idx = "  n/a" if r["image_index"] is None else f"{r['image_index']:+.2f}"
        zz = "   n/a" if z is None else f"{z:+.2f}"
        print(f"{r['post_date']:<11}{r['image_posts']:>6}{r['bullish']:>6}{r['bearish']:>6}{idx:>8}{zz:>7}  {flag}")


def export(conn, path="img_daily.json"):
    rows = daily(conn)
    Path(path).write_text(json.dumps(rows, indent=1))
    print(f"Wrote {len(rows)} days to {path}. It holds counts only, no images.")


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "refs":
        refs = load_refs()
        names = {(l, n) for l, n, _ in refs}
        for label in ("bullish", "bearish"):
            mine = sorted(n for l, n in names if l == label)
            print(f"{label}: {len(mine)} reference image(s)  {', '.join(mine)}")
        print(f"Folder: {REFS_DIR}")
    elif cmd == "test-image" and len(argv) > 2:
        with Image.open(argv[2]) as im:
            im.load()
            label, name, dist = classify(im, load_refs())
            g, r = color_fractions(im)
        print(f"label: {label}  closest ref: {name}  distance: {dist} (match needs <= {MATCH_MAX_DIST})")
        print(f"green {g:.0%}  red {r:.0%}")
    elif cmd in ("report", "export"):
        db = argv[2] if len(argv) > 2 else "biz_sentiment.db"
        conn = sqlite3.connect(db)
        report(conn) if cmd == "report" else export(conn)
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
