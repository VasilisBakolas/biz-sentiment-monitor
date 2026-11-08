"""
biz_scoring_v2.py

Drop-in replacement for sentiment_score() in biz_sentiment.py.

What changed compared with v1:
  1. Whole-word matching. "path" no longer scores as "ath", "drug" as "rug",
     "seller" as "sell", "gemstone" as "gem".
  2. Longest phrase wins and nothing is counted twice. "never selling" is one
     bullish hit; v1 also counted "sell" and "selling" inside it as bearish.
  3. Simple inflections still match: pump / pumps / pumped / pumping.
  4. Negation: a negator ("not", "never", "isn't", "no" ...) within three words
     before a term, in the same clause, flips that term. "not a scam" is bullish.
  5. Apostrophes are ignored on both sides, so "we're" and "were" are the same.

Known limits: negation is a three-word heuristic, so "not only bullish" flips
by mistake; sarcasm is not detected; terms are English only.

Usage:
    from biz_scoring_v2 import sentiment_score, analyze
    sentiment_score("never selling")      # -> 1.0
    analyze("not a scam")["hits"]         # -> what matched and why
"""

import re

VERSION = "v2a"  # stored next to each score so old and new scores can be told apart

BULLISH_WORDS = [
    "moon", "mooning", "moonshot", "accumulate", "accumulating", "undervalued", "buy the dip",
    "btfd", "breakout", "bullish", "pump", "pumping", "all time high", "ath", "hodl",
    "holding", "diamond hands", "to the moon", "supercycle", "gem", "100x", "10x", "1000x",
    "wagmi", "comfy", "we're so back", "were so back", "back on the menu", "load up",
    "loading up", "backing up the truck", "generational buy", "generational wealth",
    "life changing money", "still early", "not selling", "never selling", "cant stop buying",
    "can't stop buying", "printing money", "green candles", "green candle", "uptrend",
    "bull run", "bull market", "parabolic", "floor is in", "bottom is in", "buy signal",
    "strong hands", "conviction", "long term hold", "ltbh", "we are so back",
    # batch A: frequent in unscored posts, clear mood (Nov 2026 term report)
    "gonna make it", "going to make it", "will make it",
]

BEARISH_WORDS = [
    "rug", "rugpull", "rugged", "dump", "dumping", "dumped", "dead", "scam", "scammed",
    "bagholder", "bagholders", "bagholding", "bearish", "crash", "crashing", "crashed", "rekt",
    "capitulation", "capitulate", "sell", "selling", "sold", "exit liquidity", "ponzi",
    "top signal", "ngmi", "fud", "cope", "coping", "seethe", "seething", "rope", "it's over",
    "its over", "im done", "i'm done", "over bros", "down bad", "down only", "red candles",
    "red candle", "downtrend", "bear market", "dead cat bounce", "underwater", "in the red",
    "lost everything", "lost it all", "sell signal", "weak hands", "paper hands",
    "distribution", "top is in", "blow off top", "exit scam", "honeypot",
    # batch A: frequent in unscored posts, clear mood (Nov 2026 term report)
    "shitcoin", "fucked", "getting fucked", "go down", "going down", "goes down", "went down", "lose money", "losing money", "lost money", "to zero", "going to zero", "shut down", "killed crypto", "nobody cares",
]

# Terms are matched after normalisation (lower case, apostrophes removed).
NEGATORS = {
    "not", "no", "never", "isnt", "arent", "wasnt", "dont", "doesnt", "didnt",
    "cant", "cannot", "wont", "aint", "without", "hardly",
}

# Inflections allowed after a single-word term made of letters only.
_SUFFIX = r"(?:ing|es|ed|s|d)?"
_SUFFIXES = ("ing", "es", "ed", "s", "d")
_CLAUSE_BREAK = re.compile(r"[.!?;,:\n]")


def _norm(text):
    t = text.lower().replace("\u2019", "'").replace("\u2018", "'").replace("'", "")
    return re.sub(r"\s+", " ", t)


def _build():
    terms = {}
    for w in BULLISH_WORDS:
        terms[_norm(w)] = 1
    for w in BEARISH_WORDS:
        n = _norm(w)
        if terms.get(n) == 1:
            raise ValueError("term is in both lexicons: %r" % w)
        terms[n] = -1
    parts = []
    for term in sorted(terms, key=lambda t: (-len(t), t)):
        pat = re.escape(term).replace("\\ ", " ")
        if re.fullmatch(r"[a-z]+", term):
            pat += _SUFFIX
        parts.append(pat)
    rx = re.compile(r"(?<![a-z0-9])(?:" + "|".join(parts) + r")(?![a-z0-9])")
    return terms, rx


_TERMS, _RX = _build()


def _lookup(matched):
    if matched in _TERMS:
        return matched
    for suf in _SUFFIXES:
        if matched.endswith(suf) and matched[: -len(suf)] in _TERMS:
            return matched[: -len(suf)]
    return None


def _negated(text, start):
    before = text[max(0, start - 40):start]
    before = _CLAUSE_BREAK.split(before)[-1]
    return any(w in NEGATORS for w in re.findall(r"[a-z0-9]+", before)[-3:])


def analyze(text):
    """Return bull/bear counts, the score, and every hit with its reason."""
    t = _norm(text or "")
    hits, bull, bear = [], 0, 0
    for m in _RX.finditer(t):
        term = _lookup(m.group(0))
        if term is None:
            continue
        pol = _TERMS[term]
        neg = _negated(t, m.start())
        if neg:
            pol = -pol
        if pol > 0:
            bull += 1
        else:
            bear += 1
        hits.append({"term": term, "text": m.group(0), "polarity": pol, "negated": neg})
    total = bull + bear
    return {"bull": bull, "bear": bear, "total": total,
            "score": None if total == 0 else (bull - bear) / total, "hits": hits}


def sentiment_score(text):
    """(bullish - bearish) / total hits, or None when no sentiment words match."""
    return analyze(text)["score"]
