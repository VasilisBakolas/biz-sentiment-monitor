# Biz Sentiment Monitor

A small Python tool that reads the 4chan /biz/ board through its public JSON API, finds mentions of coins (it records BTC, ETH, XRP, SOL, LINK and DOGE; the write-up analyses BTC and ETH), scores bullish and bearish language, stores the results in SQLite, and flags days that sit more than two standard deviations from the previous 14 days.

It is a contrarian gauge of retail mood. It is not a trading signal.

## Why I built it

I wanted to know whether the chatter on /biz/ says anything about BTC and ETH prices, and to learn data analysis on real, messy data. It started out of curiosity, and I then tested it the way I would at work: hand-labelled posts, error rates and honest limits. The short answer is that it mostly does not predict prices. The write-up shows how I got there and where the method is weak.

**Interactive write-up** (replay of real data, price charts, live scoring demo, evaluation): [open the live page](https://vasilisbakolas.github.io/biz-sentiment-monitor/) or open `docs/index.html` in a browser.

## What I found

- Over 127 days (5 Jul to 8 Nov 2026), sentiment spikes did not lead BTC or ETH price moves. The window is short, so this means "no evidence yet".
- Checked against 137 posts I labelled by hand: when the scoring calls a direction it is right about 7 in 10 times, but it misses most posts with a clear mood and scores only about 37% of stored BTC and ETH posts.
- The weak point is language, not the pipeline. See the Evaluation section of the write-up.

## Files

| File | What it does |
|---|---|
| `biz_sentiment.py` | The scraper: `poll`, `loop`, `report`. Keeps the original substring score in `sentiment`, which produced the data in the write-up. With `biz_scoring_v2.py` next to it, it also stores the new score in `sentiment_v2` with a `lexicon_version` label; with `biz_images.py` next to it, it also records image moods. Without those files it behaves exactly as the original. |
| `biz_images.py` | Optional image-mood add-on (see below). |
| `biz_scoring_v2.py` | Improved scoring: whole words, longest phrase wins, negation ("not selling"), more terms. Running live since 8 Nov 2026, stored beside the original score. |
| `tests/` | Checks for the v2 scoring, the image add-on, and the scraper hook. |
| `term_report.py` | Lists which words and phrases appear most in the stored posts. |
| `docs/index.html` | The write-up page. |

## Run it

```
pip install -r requirements.txt
python biz_sentiment.py poll      # one poll
python biz_sentiment.py loop      # poll every 3 minutes
python biz_sentiment.py report    # z-score summary
python tests/test_biz_scoring_v2.py
python tests/test_biz_images.py
python tests/test_scraper_hook.py
```

The database file is created in the current folder and is git-ignored. No post text is committed to this repository.

## Image mood (experimental)

/biz/ is an image board, so much of the mood is in the pictures. `biz_images.py` adds a second signal:

- For each post with an image it fetches the small preview the board already serves, computes a visual fingerprint and the share of green and red pixels, and discards the picture. Only numbers and a label are stored; nothing is saved to disk or republished.
- You put example memes in `meme_refs/bullish/` (bull cartoons, green candles) and `meme_refs/bearish/` (pink wojak). A close match, including mirrored or recompressed copies, labels the post bullish (+1) or bearish (-1).
- Colour is stored but not used for the label, because a red chart can mean either mood.
- Collected from the day it is switched on. Threads expire, so history cannot be filled in.
- Useful commands: `python biz_images.py refs`, `python biz_images.py test-image FILE`, `python biz_images.py report`, `python biz_images.py export`.

Status: running live since 8 Nov 2026. It has not been checked against hand labels yet, and it needs the same evaluation as the text scoring before any result is claimed.

## Known limits

- The stored snippet is cut at 200 characters, so rescoring old data can only use the snippet.
- The stored scores use the original substring matching ("path" scores as "ath", "drug" as "rug").
- Sarcasm, spam and posts about a different coin than the one named are not handled.
- Hand labels come from one person.
- Image matching only recognises the memes you supply, and a cropped or heavily edited meme may be missed.

## Data and etiquette

Uses the board's public read-only JSON API with a descriptive User-Agent and a three-minute poll interval. Personal, non-commercial research.

## Disclaimer

Personal, non-commercial research project. Nothing here is financial or investment advice, and the gauge is not a trading signal. Not affiliated with 4chan or any exchange.

## Author

Vasileios Bakolas, Berlin, 2026. [LinkedIn](https://www.linkedin.com/in/vasilis-bakolas-b7188181/) · [GitHub](https://github.com/VasilisBakolas)

## Licence

MIT
