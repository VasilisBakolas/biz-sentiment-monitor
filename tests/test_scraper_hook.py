"""The scraper must behave the same with and without the image add-on."""
import os, sys, time, sqlite3
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import biz_sentiment as bs
import biz_images as bi
import biz_scoring_v2 as v2

now = time.time()
THREAD = [
    {"no": 100, "time": now - 60, "com": "BTC is mooring, not selling", "tim": 1, "md5": "x"},
    {"no": 101, "time": now - 50, "tim": 2, "md5": "y"},                       # image only, no text
    {"no": 102, "time": now - 40, "com": "nothing about coins here"},
]
bs.fetch_catalog = lambda: [{"no": 900, "replies": 2}]
bs.fetch_thread = lambda tid: THREAD
bs.time.sleep = lambda s: None
bi.fetch_thumb = lambda tim: None          # previews "gone": no network
bi.time.sleep = lambda s: None

def run(with_images):
    bs.biz_images = bi if with_images else None
    conn = bs.init_db(":memory:")
    bs.poll_once(conn)
    posts = conn.execute("SELECT post_id, ticker, sentiment FROM posts").fetchall()
    imgs = conn.execute("SELECT name FROM sqlite_master WHERE name='post_images'").fetchall()
    n_img = conn.execute("SELECT COUNT(*) FROM post_images").fetchone()[0] if imgs else None
    return posts, n_img

p_off, n_off = run(False)
p_on, n_on = run(True)
fails = 0
def check(name, c):
    global fails
    print(("ok   " if c else "FAIL ") + name); fails += 0 if c else 1
check("text results identical with and without the add-on", p_off == p_on and len(p_on) == 1)
check("no image table without the add-on", n_off is None)
bs.biz_scoring_v2 = None; bs.biz_images = None
conn0 = bs.init_db(":memory:"); bs.poll_once(conn0)
check("without v2 file: new columns stay empty", conn0.execute("SELECT sentiment_v2, lexicon_version FROM posts").fetchall() == [(None, None)])
bs.biz_scoring_v2 = v2
conn1 = bs.init_db(":memory:"); bs.poll_once(conn1)
row = conn1.execute("SELECT sentiment, sentiment_v2, lexicon_version FROM posts").fetchone()
check("with v2: both scores stored, old one unchanged", row[0] == p_off[0][2] and row[1] == 1.0 and row[2] == v2.VERSION)
# an existing database from before gets the new columns without losing rows
old = sqlite3.connect(":memory:")
old.execute("CREATE TABLE posts (post_id INTEGER, thread_id INTEGER, ts TEXT, post_date TEXT, poster_id TEXT, ticker TEXT, sentiment REAL, snippet TEXT, PRIMARY KEY (post_id, ticker))")
old.execute("INSERT INTO posts VALUES (1,1,'t','2026-01-01',NULL,'BTC',0.5,'x')")
import types
_c = sqlite3.connect
sqlite3.connect = lambda *_a, **_k: old
try: migrated = bs.init_db("ignored")
finally: sqlite3.connect = _c
check("old database migrates and keeps its rows", migrated.execute("SELECT post_id, sentiment, sentiment_v2 FROM posts").fetchall() == [(1, 0.5, None)])
check("image-only posts are noted with the add-on", n_on == 2)
print(f"\n{fails} checks failed"); sys.exit(1 if fails else 0)
