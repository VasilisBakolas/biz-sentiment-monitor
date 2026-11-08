"""
term_report.py

Counts which words and two-word phrases appear most often in posts that got NO
sentiment score, so you can see which mood words the lexicon is missing.

It reads biz_sentiment.db and writes biz_terms.json next to this script.
The output has word counts only. No post text, no poster IDs.

Run it from the folder that holds biz_sentiment.db (your home folder):
    python3 term_report.py
Needs only Python's standard library.
"""
import json, os, re, sqlite3, sys
from collections import Counter

DB = os.path.expanduser("~/biz_sentiment.db")
if len(sys.argv) > 1:
    DB = os.path.expanduser(sys.argv[1])
if not os.path.exists(DB):
    sys.exit("Database not found at " + DB + "\nRun again with its path:  python3 term_report.py /path/to/biz_sentiment.db")

STOP = set("""a about after all also am an and any are as at be because been but by can could did do does
for from get got had has have he her him his how i if in into is it its just like me more most my no not of
on one or our out over she so some than that the their them then there these they this to up us was we were
what when which who will with would you your im ive dont thats its youre cant wont isnt theyre
https http www com gt amp quot nbsp""".split())
TICK = set("btc eth xrp sol link doge bitcoin ethereum ripple solana chainlink dogecoin".split())

conn = sqlite3.connect(DB)
cur = conn.cursor()

# coverage: share of distinct posts with and without a sentiment score
tot = cur.execute("SELECT COUNT(DISTINCT post_id) FROM posts").fetchone()[0]
scored = cur.execute("SELECT COUNT(DISTINCT post_id) FROM posts WHERE sentiment IS NOT NULL").fetchone()[0]
by_t = {}
for t, n, s in cur.execute("SELECT ticker, COUNT(*), SUM(sentiment IS NOT NULL) FROM posts GROUP BY ticker"):
    by_t[t] = {"rows": n, "scored": int(s or 0), "scored_share": round((s or 0) / n, 3) if n else None}

uni, bi, n_docs = Counter(), Counter(), 0
for (snip,) in cur.execute("SELECT snippet FROM posts WHERE sentiment IS NULL GROUP BY post_id"):
    text = (snip or "").lower().replace("'", "").replace("’", "")
    words = [w for w in re.findall(r"[a-z0-9]+", text)]
    n_docs += 1
    keep = [w for w in words if len(w) > 2 and w not in STOP and w not in TICK and not w.isdigit()]
    uni.update(set(keep))                                     # document frequency, not raw count
    bi.update({a + " " + b for a, b in zip(words, words[1:])
               if a not in STOP and b not in STOP and a not in TICK and b not in TICK and len(a) > 1 and len(b) > 1})

out = {
    "posts_total": tot, "posts_scored": scored,
    "posts_unscored": tot - scored,
    "scored_share": round(scored / tot, 3) if tot else None,
    "by_ticker": by_t,
    "unscored_posts_used": n_docs,
    "note": "snippets are cut at 200 characters, so long posts are only partly counted",
    "top_words": [[w, c] for w, c in uni.most_common(300) if c >= 5],
    "top_two_word_phrases": [[w, c] for w, c in bi.most_common(300) if c >= 5],
}
dest = os.path.join(os.path.dirname(os.path.abspath(__file__)), "biz_terms.json")
json.dump(out, open(dest, "w"), indent=1)
print("Posts total %d, with a sentiment score %d (%.0f%%)." % (tot, scored, 100 * scored / tot if tot else 0))
print("Wrote " + dest)
