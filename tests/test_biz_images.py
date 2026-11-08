"""Checks for biz_images.py using made-up pictures. No network, no real images."""
import os, random, sqlite3, sys, tempfile, time
from io import BytesIO
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from PIL import Image, ImageDraw, ImageOps, ImageFilter
import biz_images as bi

fails = 0
def check(name, cond, extra=""):
    global fails
    print(("ok   " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        fails += 1

def art(seed, size=300):
    """A made-up cartoon: random shapes. Same seed = same picture."""
    rnd = random.Random(seed)
    im = Image.new("RGB", (size, size), tuple(rnd.randrange(256) for _ in range(3)))
    d = ImageDraw.Draw(im)
    for _ in range(14):
        x0, y0 = rnd.randrange(size - 40), rnd.randrange(size - 40)
        box = [x0, y0, x0 + rnd.randrange(30, 160), y0 + rnd.randrange(30, 160)]
        col = tuple(rnd.randrange(256) for _ in range(3))
        (d.ellipse if rnd.random() < .5 else d.rectangle)(box, fill=col)
    return im

def thumb(im):
    """What the board serves: a small jpeg preview."""
    t = im.copy(); t.thumbnail((125, 125))
    b = BytesIO(); t.save(b, "JPEG", quality=70); b.seek(0)
    return Image.open(b)

tmp = tempfile.mkdtemp()
for lab, seeds in (("bullish", (1, 2)), ("bearish", (3, 4))):
    os.makedirs(f"{tmp}/{lab}")
    for s in seeds:
        art(s).save(f"{tmp}/{lab}/{lab}_{s}.png")
refs = bi.load_refs(tmp)
check("loads 4 refs, each hashed twice", len(refs) == 8, len(refs))

# same picture, degraded the way reposts are
for s, lab in ((1, "bullish"), (2, "bullish"), (3, "bearish"), (4, "bearish")):
    got = bi.classify(thumb(art(s)), refs)
    check(f"preview of ref {s} -> {lab}", got[0] == lab, got)
check("mirrored repost still matches", bi.classify(thumb(ImageOps.mirror(art(1))), refs)[0] == "bullish")
check("blurred repost still matches", bi.classify(thumb(art(3).filter(ImageFilter.GaussianBlur(2))), refs)[0] == "bearish")
unrelated = [bi.classify(thumb(art(s)), refs)[0] for s in range(100, 130)]
check("30 unrelated pictures get no label", all(u is None for u in unrelated), unrelated.count(None))
check("no refs -> no label", bi.classify(art(1), [])[0] is None)

# a picture equally close to both classes is left unlabelled
same = Image.open(f"{tmp}/bullish/bullish_1.png")
amb = [("bullish", "a", bi.dhash(same)), ("bearish", "b", bi.dhash(same))]
check("tie between classes -> no label", bi.classify(same, amb)[0] is None)

# colour features
g, r = bi.color_fractions(Image.new("RGB", (60, 60), (0, 200, 0)))
check("solid green", g > .95 and r < .05, (g, r))
g, r = bi.color_fractions(Image.new("RGB", (60, 60), (210, 20, 20)))
check("solid red", r > .95 and g < .05, (g, r))
g, r = bi.color_fractions(Image.new("RGB", (60, 60), (128, 128, 128)))
check("grey is neither", g == 0 and r == 0)

# database flow with a fake downloader
conn = sqlite3.connect(":memory:")
bi._REFS = refs
bi._REFS_SIG = bi._refs_signature(bi.REFS_DIR)   # fake refs count as "up to date"
bi._REFS_CHECKED = time.time() + 10**9
now = time.time()
calls = []
def fake_fetch(tim):
    calls.append(tim)
    if tim == 404: return None
    if tim == 500: raise RuntimeError("boom")
    return {11: thumb(art(1)), 12: thumb(art(3)), 13: thumb(art(200))}[tim]
posts = [
    {"no": 1, "tim": 11, "md5": "A", "time": now - 60},
    {"no": 2, "tim": 12, "md5": "B", "time": now - 50},
    {"no": 3, "tim": 13, "md5": "C", "time": now - 40},
    {"no": 4, "tim": 11, "md5": "A", "time": now - 30},      # same file again
    {"no": 5, "tim": 404, "md5": "D", "time": now - 20},
    {"no": 6, "tim": 500, "md5": "E", "time": now - 10},
    {"no": 7, "time": now - 5},                              # no image
    {"no": 8, "tim": 11, "md5": "A", "time": now - 99 * 3600},  # too old
]
for p in posts:
    bi.note_post(conn, p, 900, now=now)
    bi.note_post(conn, p, 900, now=now)                      # twice: no duplicate row
n = conn.execute("SELECT COUNT(*) FROM post_images").fetchone()[0]
check("6 image posts noted once each (no-image and too-old posts skipped)", n == 6, n)
done = bi.process_pending(conn, fetch=fake_fetch, now=now, delay=0)
st = dict(conn.execute("SELECT post_id, status FROM post_images"))
check("states", st == {1: "done", 2: "done", 3: "done", 4: "done", 5: "gone", 6: "failed"}, st)
check("repeat file not downloaded twice", calls.count(11) == 1, calls)
lab = dict(conn.execute("SELECT post_id, img_label FROM post_images"))
check("labels", lab[1] == "bullish" and lab[2] == "bearish" and lab[3] is None and lab[4] == "bullish", lab)
d = bi.daily(conn)
check("daily counts", len(d) == 1 and d[0]["bullish"] == 2 and d[0]["bearish"] == 1 and d[0]["scored"] == 4, d)
check("daily index = (2-1)/3", abs(d[0]["image_index"] - 1 / 3) < 1e-9, d[0]["image_index"])
check("nothing but numbers stored", {r[1] for r in conn.execute("PRAGMA table_info(post_images)")}.isdisjoint({"image", "blob", "data", "path"}))
# stale pending rows are dropped
conn.execute("INSERT INTO post_images (post_id, thread_id, post_epoch, post_date, tim, md5, status) VALUES (50, 1, ?, '2000-01-01', 11, 'Z', 'pending')", (now - 20 * 3600,))
bi.process_pending(conn, fetch=fake_fetch, now=now, delay=0)
check("pending older than the limit becomes gone", conn.execute("SELECT status FROM post_images WHERE post_id=50").fetchone()[0] == "gone")
# errors never escape
bad = sqlite3.connect(":memory:"); bad.close()
try:
    bi.note_post(bad, {"no": 1, "tim": 1}, 1); bi.process_pending(bad); ok = True
except Exception:
    ok = False
check("closed database does not raise", ok)

# references added or removed while running are picked up without a restart
import shutil
rd = tempfile.mkdtemp()
os.makedirs(rd + "/bullish"); os.makedirs(rd + "/bearish")
src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_ref_tmp.png")
Image.new("RGB", (40, 40), (10, 200, 10)).save(src)
shutil.copy(src, rd + "/bullish/a.png")
saved = (bi.REFS_DIR, bi._REFS, bi._REFS_SIG, bi._REFS_CHECKED)
bi.REFS_DIR = bi.Path(rd); bi._REFS = None
n0 = len(bi.load_refs()) // 2
shutil.copy(src, rd + "/bullish/b.png"); bi._REFS_CHECKED = 0
n1 = len(bi.load_refs()) // 2
os.remove(rd + "/bullish/a.png"); bi._REFS_CHECKED = 0
n2 = len(bi.load_refs()) // 2
bi.REFS_DIR, bi._REFS, bi._REFS_SIG, bi._REFS_CHECKED = saved
os.remove(src)
check("new reference picture picked up without restart", (n0, n1, n2) == (1, 2, 1), (n0, n1, n2))

print(f"\n{fails} checks failed")
sys.exit(1 if fails else 0)
