import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
"""Run with:  python3 test_biz_scoring_v2.py   (from the folder that holds biz_scoring_v2.py)"""
from biz_scoring_v2 import sentiment_score, analyze

CASES = [
    # text, expected score
    ("not selling", 1.0),
    ("never selling, wagmi", 1.0),
    ("im not bearish", 1.0),
    ("this is not a scam", 1.0),
    ("bitcoin is not dead", 1.0),
    ("not moon", -1.0),
    ("we are so back", 1.0),
    ("we're so back", 1.0),
    ("BTC mooning, ath incoming", 1.0),
    ("ETH is dead. Total rug, exit liquidity, ngmi.", -1.0),
    ("it pumped hard then dumped", 0.0),
    ("sellers are dumping", -1.0),
    ("Took the path to the drug seller, bought a gemstone.", None),
    ("nothing here", None),
    ("", None),
    # batch A additions
    ("total shitcoin, stay away", -1.0),
    ("shitcoins everywhere", -1.0),
    ("we're fucked", -1.0),
    ("getting fucked again", -1.0),
    ("this is going down hard", -1.0),
    ("it will never go down", 1.0),
    ("you will lose money", -1.0),
    ("nobody cares about this coin", -1.0),
    ("gonna make it", 1.0),
    ("never going to make it", -1.0),
    ("price went to zero", -1.0),
    ("zero fees", None),
]
bad = 0
for text, want in CASES:
    got = sentiment_score(text)
    ok = (got is None and want is None) or (got is not None and want is not None and abs(got - want) < 1e-9)
    print(("ok   " if ok else "FAIL ") + repr(text) + " -> " + str(got))
    bad += not ok
# a term may only appear once in the output of a single phrase
assert analyze("never selling")["total"] == 1, "overlapping terms were counted twice"
print("\n%d of %d checks failed" % (bad, len(CASES)))
raise SystemExit(1 if bad else 0)
