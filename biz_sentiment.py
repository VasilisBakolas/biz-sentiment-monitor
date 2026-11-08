"""
biz_sentiment.py

A starter tool for scraping 4chan's /biz/ board via its public JSON API,
extracting crypto ticker mentions, scoring basic bullish/bearish sentiment,
and storing results in SQLite for historical tracking.

Intended use: a crude, contrarian retail-sentiment gauge to overlay against
your own on-chain / TA analysis -- NOT a standalone trading signal.

Usage:
    python biz_sentiment.py poll        # one-off poll of current catalog
    python biz_sentiment.py loop        # poll every N seconds forever
    python biz_sentiment.py report      # print recent sentiment summary from DB
"""

import html
import math
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone

import requests

# Optional image-mood add-on. If biz_images.py is not next to this file, nothing changes.
try:
    import biz_images
except ImportError as _e:
    biz_images = None
    if getattr(_e, "name", "") != "biz_images":  # the add-on exists but a library is missing
        print(f"image add-on disabled: {_e}")

# Optional improved word scoring (whole words, negation, more terms). It is stored
# NEXT TO the original score, never instead of it. Without the file, nothing changes.
try:
    import biz_scoring_v2
except ImportError:
    biz_scoring_v2 = None

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BOARD = "biz"
CATALOG_URL = f"https://a.4cdn.org/{BOARD}/catalog.json"
THREAD_URL = f"https://a.4cdn.org/{BOARD}/thread/{{tid}}.json"
DB_PATH = "biz_sentiment.db"
POLL_INTERVAL_SECONDS = 180  # 4chan asks for ~1 req/sec max, be conservative
USER_AGENT = "biz-sentiment-research/0.1 (personal, non-commercial)"

# Known tickers/coin names worth tracking -- extend as needed.
TICKERS = {
    "BTC": [r"\bBTC\b", r"\bbitcoin\b"],
    "ETH": [r"\bETH\b", r"\bethereum\b"],
    "XRP": [r"\bXRP\b", r"\bripple\b"],
    "SOL": [r"\bSOL\b", r"\bsolana\b"],
    "LINK": [r"\bLINK\b", r"\bchainlink\b"],
    "DOGE": [r"\bDOGE\b", r"\bdogecoin\b"],
}

BULLISH_WORDS = [
    "moon", "mooning", "moonshot", "accumulate", "accumulating", "undervalued",
    "buy the dip", "btfd", "breakout", "bullish", "pump", "pumping",
    "all time high", "ath", "hodl", "holding", "diamond hands", "to the moon",
    "supercycle", "gem", "100x", "10x", "1000x", "wagmi", "comfy", "we're so back",
    "were so back", "back on the menu", "load up", "loading up", "backing up the truck",
    "generational buy", "generational wealth", "life changing money", "still early",
    "not selling", "never selling", "cant stop buying", "can't stop buying",
    "printing money", "green candles", "green candle", "uptrend", "bull run",
    "bull market", "parabolic", "floor is in", "bottom is in", "buy signal",
    "strong hands", "conviction", "long term hold", "ltbh",
]

BEARISH_WORDS = [
    "rug", "rugpull", "rugged", "dump", "dumping", "dumped", "dead", "scam",
    "scammed", "bagholder", "bagholders", "bagholding", "bearish", "crash",
    "crashing", "crashed", "rekt", "capitulation", "capitulate",
    "sell", "selling", "sold", "exit liquidity", "ponzi", "top signal", "ngmi",
    "fud", "cope", "coping", "seethe", "seething", "rope", "it's over",
    "its over", "im done", "i'm done", "over bros", "down bad", "down only",
    "red candles", "red candle", "downtrend", "bear market", "dead cat bounce",
    "underwater", "in the red", "lost everything", "lost it all", "sell signal",
    "weak hands", "paper hands", "distribution", "top is in", "blow off top",
    "exit scam", "honeypot",
]

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get(url):
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_catalog():
    """Return list of thread dicts from the /biz/ catalog (all pages flattened)."""
    pages = _get(CATALOG_URL)
    threads = []
    for page in pages:
        threads.extend(page.get("threads", []))
    return threads


def fetch_thread(thread_id):
    """Return list of post dicts (OP + replies) for a given thread id."""
    data = _get(THREAD_URL.format(tid=thread_id))
    return data.get("posts", [])


# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------

TAG_RE = re.compile(r"<[^>]+>")


def clean_text(raw):
    """Strip HTML tags/entities from a 4chan post 'com' field."""
    if not raw:
        return ""
    text = raw.replace("<br>", "\n")
    text = TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return text.strip()


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def find_tickers(text):
    text_lower = text.lower()
    found = set()
    for ticker, patterns in TICKERS.items():
        for pat in patterns:
            if re.search(pat, text_lower, flags=re.IGNORECASE):
                found.add(ticker)
                break
    return found


def sentiment_score(text):
    """
    Very simple lexicon-based score: (bullish_hits - bearish_hits) / total_hits.
    Returns None if no sentiment words found (rather than a misleading 0).
    """
    text_lower = text.lower()
    bull = sum(text_lower.count(w) for w in BULLISH_WORDS)
    bear = sum(text_lower.count(w) for w in BEARISH_WORDS)
    total = bull + bear
    if total == 0:
        return None
    return (bull - bear) / total


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def init_db(path=DB_PATH):
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            post_id INTEGER,
            thread_id INTEGER,
            ts TEXT,
            post_date TEXT,
            poster_id TEXT,
            ticker TEXT,
            sentiment REAL,
            snippet TEXT,
            PRIMARY KEY (post_id, ticker)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS thread_seen (
            thread_id INTEGER PRIMARY KEY,
            last_replies INTEGER
        )
    """)
    for col, kind in (("sentiment_v2", "REAL"), ("lexicon_version", "TEXT")):
        try:
            conn.execute(f"ALTER TABLE posts ADD COLUMN {col} {kind}")
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()
    return conn


def already_processed_thread(conn, thread_id, reply_count):
    row = conn.execute(
        "SELECT last_replies FROM thread_seen WHERE thread_id = ?", (thread_id,)
    ).fetchone()
    if row is None:
        return False
    return row[0] >= reply_count


def mark_thread_seen(conn, thread_id, reply_count):
    conn.execute(
        "INSERT INTO thread_seen (thread_id, last_replies) VALUES (?, ?) "
        "ON CONFLICT(thread_id) DO UPDATE SET last_replies=excluded.last_replies",
        (thread_id, reply_count),
    )


def store_post(conn, post_id, thread_id, post_epoch, poster_id, ticker, sentiment, snippet,
               sentiment_v2=None, lexicon_version=None):
    post_dt = datetime.fromtimestamp(post_epoch, tz=timezone.utc)
    ts = post_dt.isoformat()
    post_date = post_dt.date().isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO posts "
        "(post_id, thread_id, ts, post_date, poster_id, ticker, sentiment, snippet, "
        "sentiment_v2, lexicon_version) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (post_id, thread_id, ts, post_date, poster_id, ticker, sentiment, snippet[:200],
         sentiment_v2, lexicon_version),
    )


# ---------------------------------------------------------------------------
# Main poll routine
# ---------------------------------------------------------------------------

def poll_once(conn):
    catalog = fetch_catalog()
    print(f"[{datetime.now(timezone.utc).isoformat()}] {len(catalog)} threads in catalog")

    new_post_count = 0

    for thread_summary in catalog:
        thread_id = thread_summary["no"]
        reply_count = thread_summary.get("replies", 0)

        if already_processed_thread(conn, thread_id, reply_count):
            continue

        try:
            posts = fetch_thread(thread_id)
        except requests.HTTPError:
            # thread 404'd between catalog fetch and thread fetch -- skip
            continue

        for post in posts:
            if biz_images is not None:
                biz_images.note_post(conn, post, thread_id)  # records image posts, never raises
            text = clean_text(post.get("com", "") or "")
            if not text:
                continue

            tickers = find_tickers(text)
            if not tickers:
                continue  # only store posts that mention a tracked ticker

            score = sentiment_score(text)
            score_v2, lex_ver = None, None
            if biz_scoring_v2 is not None:
                try:
                    score_v2, lex_ver = biz_scoring_v2.sentiment_score(text), biz_scoring_v2.VERSION
                except Exception as e:  # never let the new scoring stop collection
                    print(f"  v2 scoring error: {e}")
            post_epoch = post.get("time", time.time())
            poster_id = post.get("id")  # only present if board has poster IDs enabled
            for ticker in tickers:
                store_post(conn, post["no"], thread_id, post_epoch, poster_id, ticker, score, text,
                           score_v2, lex_ver)
                new_post_count += 1

        mark_thread_seen(conn, thread_id, reply_count)
        time.sleep(1.1)  # be polite to the API between thread fetches

    conn.commit()
    print(f"  stored {new_post_count} new ticker-mentioning posts")
    if biz_images is not None:
        n_img = biz_images.process_pending(conn)  # downloads small previews, keeps numbers only
        print(f"  scored {n_img} image posts")


def loop(conn):
    while True:
        try:
            poll_once(conn)
        except requests.RequestException as e:
            print(f"  request error: {e}")
        time.sleep(POLL_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _mean_std(values):
    n = len(values)
    if n == 0:
        return None, None
    mean = sum(values) / n
    if n < 2:
        return mean, None
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    return mean, math.sqrt(variance)


def report(conn, baseline_days=14):
    print("\n--- Ticker overview (all time) ---")
    print("  Note: 'threads' and 'posters' are de-duplicated counts -- these")
    print("  matter more than raw 'posts', since samefagging inflates volume.")
    rows = conn.execute("""
        SELECT ticker,
               COUNT(*) AS posts,
               COUNT(DISTINCT thread_id) AS threads,
               COUNT(DISTINCT COALESCE(poster_id, 'unknown-' || post_id)) AS posters,
               AVG(sentiment) AS avg_sent
        FROM posts
        GROUP BY ticker
        ORDER BY posts DESC
    """).fetchall()
    for ticker, posts, threads, posters, avg_sent in rows:
        avg_str = f"{avg_sent:+.2f}" if avg_sent is not None else "n/a"
        print(f"  {ticker:6s} posts={posts:4d}  threads={threads:3d}  "
              f"posters={posters:4d}  avg_sentiment={avg_str}")

    print(f"\n--- Sentiment/volume extremity vs trailing {baseline_days}-day baseline ---")
    print("  |z| >= 2.0 flags an unusually stretched day for that ticker --")
    print("  read EXTREME BULLISH as a caution flag, EXTREME BEARISH as a")
    print("  potential contrarian buy signal, not the other way around.")

    tickers = [r[0] for r in rows]
    for ticker in tickers:
        daily = conn.execute("""
            SELECT post_date, COUNT(*) AS n, AVG(sentiment) AS avg_sent
            FROM posts
            WHERE ticker = ?
            GROUP BY post_date
            ORDER BY post_date ASC
        """, (ticker,)).fetchall()

        if len(daily) < 3:
            print(f"  {ticker:6s}  not enough daily history yet ({len(daily)} day(s) seen)")
            continue

        *history, today = daily
        history = history[-baseline_days:]

        vol_mean, vol_std = _mean_std([d[1] for d in history])
        sent_values = [d[2] for d in history if d[2] is not None]
        sent_mean, sent_std = _mean_std(sent_values)

        today_date, today_n, today_sent = today

        vol_z = (today_n - vol_mean) / vol_std if vol_std else None
        sent_z = (
            (today_sent - sent_mean) / sent_std
            if (today_sent is not None and sent_std)
            else None
        )

        def _flag(z):
            if z is None:
                return ""
            if z >= 2.0:
                return "  <- EXTREME HIGH"
            if z <= -2.0:
                return "  <- EXTREME LOW"
            return ""

        vol_z_str = f"{vol_z:+.2f}" if vol_z is not None else "n/a"
        sent_z_str = f"{sent_z:+.2f}" if sent_z is not None else "n/a"
        print(f"  {ticker:6s} [{today_date}]  volume_z={vol_z_str}{_flag(vol_z)}  "
              f"sentiment_z={sent_z_str}{_flag(sent_z)}")

    print("\n--- Most recent 10 ticker-mentioning posts ---")
    rows = conn.execute(
        "SELECT ts, ticker, sentiment, snippet FROM posts ORDER BY ts DESC LIMIT 10"
    ).fetchall()
    for ts, ticker, sentiment, snippet in rows:
        sent_str = f"{sentiment:+.2f}" if sentiment is not None else "n/a"
        print(f"  [{ts}] {ticker} ({sent_str}): {snippet[:100]!r}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "poll"
    conn = init_db()

    if mode == "poll":
        poll_once(conn)
    elif mode == "loop":
        loop(conn)
    elif mode == "report":
        report(conn)
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
