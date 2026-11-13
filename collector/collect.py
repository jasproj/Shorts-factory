"""Question collector — pulls real bio bay questions, clusters duplicates, ranks by demand.

Sources (each optional; missing creds = skipped with a logged reason, never silent):
  youtube  — comments on top bio bay videos (YouTube Data API v3, env YOUTUBE_API_KEY)
  reddit   — r/PuertoRico, r/travel, r/vieques search (public JSON)
  suggest  — Google autocomplete for "bio bay ..." question stems (proxy for search demand)
  seed     — collector/seed.jsonl (manual / web-research rows)

Output: data/questions.json  (ranked clusters)  +  SQLite data/factory.db (raw rows)
Usage:  python3 collector/collect.py [--sources youtube,reddit,suggest,seed] [--top 20]
"""
import argparse, json, math, os, re, sqlite3, sys, time
import urllib.parse, urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "factory.db")
OUT = os.path.join(ROOT, "data", "questions.json")
SEED = os.path.join(ROOT, "collector", "seed.jsonl")

TOPIC_RE = re.compile(r"bio\s*-?bay|biolumin|glow(ing)?\s+(bay|water)|mosquito bay|laguna grande|la parguera|fajardo|vieques", re.I)
Q_STARTS = ("how", "what", "when", "where", "which", "why", "who", "is", "are", "can", "could", "should", "do", "does", "did", "will", "would", "worth", "any")
YT_QUERIES = ["bioluminescent bay puerto rico", "mosquito bay vieques", "laguna grande fajardo bio bay", "la parguera bioluminescent", "bio bay kayak tour"]
REDDIT_SUBS = ["PuertoRico", "travel", "vieques", "solotravel"]
SUGGEST_STEMS = ["bio bay puerto rico", "can you swim in bio bay", "is bio bay worth it", "best time to see bio bay", "mosquito bay vieques", "laguna grande fajardo"]
STOP = set("the a an is are to of in on for and or it you i be can do does at with this that my we me there any your just so really actually puerto rico pr".split())


def http_json(url, headers=None, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "wander-shorts-collector/0.1", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def is_question(text):
    t = text.strip().lower()
    return "?" in t or t.split(" ", 1)[0] in Q_STARTS


def split_questions(text):
    parts = re.split(r"(?<=[?])\s+|\n+", text)
    return [p.strip() for p in parts if 12 <= len(p.strip()) <= 220 and is_question(p) and TOPIC_RE.search(text)]


# ---------------- sources ----------------
def src_youtube(log):
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        log("youtube: SKIPPED — no YOUTUBE_API_KEY"); return []
    rows = []
    for q in YT_QUERIES:
        s = http_json("https://www.googleapis.com/youtube/v3/search?" + urllib.parse.urlencode(
            {"part": "id", "q": q, "type": "video", "maxResults": 10, "order": "viewCount", "key": key}))
        for it in s.get("items", []):
            vid = it["id"]["videoId"]
            try:
                c = http_json("https://www.googleapis.com/youtube/v3/commentThreads?" + urllib.parse.urlencode(
                    {"part": "snippet", "videoId": vid, "maxResults": 100, "order": "relevance", "textFormat": "plainText", "key": key}))
            except Exception as e:
                log(f"youtube: comments off/err on {vid}: {e}"); continue
            for th in c.get("items", []):
                sn = th["snippet"]["topLevelComment"]["snippet"]
                for qq in split_questions(sn["textDisplay"]):
                    rows.append(dict(source="youtube", ref=vid, text=qq, likes=int(sn.get("likeCount", 0)), ts=sn["publishedAt"]))
    log(f"youtube: {len(rows)} questions"); return rows


def src_reddit(log):
    rows = []
    for sub in REDDIT_SUBS:
        url = f"https://www.reddit.com/r/{sub}/search.json?" + urllib.parse.urlencode({"q": "bio bay OR bioluminescent", "restrict_sr": 1, "sort": "new", "limit": 100, "t": "year"})
        try:
            d = http_json(url)
        except Exception as e:
            log(f"reddit r/{sub}: ERROR {e}"); continue
        for ch in d.get("data", {}).get("children", []):
            p = ch["data"]
            for qq in split_questions(f"{p.get('title','')}\n{p.get('selftext','')}"):
                rows.append(dict(source="reddit", ref=p.get("permalink", ""), text=qq, likes=int(p.get("score", 0)) + int(p.get("num_comments", 0)),
                                 ts=datetime.fromtimestamp(p.get("created_utc", time.time()), timezone.utc).isoformat()))
    log(f"reddit: {len(rows)} questions"); return rows


def src_suggest(log):
    rows = []
    now = datetime.now(timezone.utc).isoformat()
    for stem in SUGGEST_STEMS:
        try:
            d = http_json("https://suggestqueries.google.com/complete/search?" + urllib.parse.urlencode({"client": "firefox", "q": stem}))
        except Exception as e:
            log(f"suggest '{stem}': ERROR {e}"); continue
        for rank, s in enumerate(d[1] if len(d) > 1 else []):
            if TOPIC_RE.search(s):
                rows.append(dict(source="suggest", ref=stem, text=s, likes=max(1, 10 - rank) * 5, ts=now))
    log(f"suggest: {len(rows)} queries"); return rows


def src_seed(log):
    if not os.path.exists(SEED):
        log("seed: SKIPPED — no seed.jsonl"); return []
    rows = [json.loads(l) for l in open(SEED) if l.strip()]
    log(f"seed: {len(rows)} rows"); return rows


SOURCES = {"youtube": src_youtube, "reddit": src_reddit, "suggest": src_suggest, "seed": src_seed}

# ---------------- clustering + ranking ----------------
# Intent map: canonical question per intent. Keyword hit => assigned. Unmatched => fuzzy clusters.
INTENTS = [
    ("swim", "Can you swim in the bio bay?", r"\bswim|get in the water|jump in"),
    ("brightest", "Which bio bay is the brightest?", r"bright|best (bio )?bay|which (bio )?bay|vieques or fajardo|fajardo or vieques|mosquito .* (vs|or) |better than|so much better|almost equal"),
    ("getting", "How do you get to the bio bay from San Juan?", r"san juan|ferry|get(ting)? to|overnight|stay .*vieques|plane|drive|uber|transport|pickup|how far"),
    ("moon", "Does the moon ruin the bio bay?", r"\bmoon"),
    ("worth", "Is the bio bay tour worth it?", r"worth|overrated|disappoint|scam|tourist trap"),
    ("closed", "Is the bio bay open / still glowing?", r"closed|still glow|gone dark|go dark|dying|dead|open (now|again)|hurricane maria|fiona"),
    ("when", "What's the best time/month to see the bio bay?", r"best (time|month|season)|what month|time of year|hurricane|rain"),
    ("photo", "Can you photograph the glow?", r"photo|camera|picture|film|gopro|iphone|video"),
    ("see", "What does the bio bay actually look like?", r"look like|how bright|can you (actually )?see|real(ly)? glow|is it real"),
    ("kayak", "Is the kayak hard? Do I need experience?", r"kayak.*(hard|difficult|experience|beginner|tired|kids|tip)|clear.?bottom|electric boat|pontoon"),
    ("cost", "How much does a bio bay tour cost?", r"cost|price|how much|\$|cheap|expensive"),
    ("bring", "What should you bring or wear?", r"bring|wear|bug|mosquito(es)? (bite|spray)|shoes|repellent"),
    ("why", "Why does the bay glow?", r"why .*glow|what makes|dinoflag|plankton|science"),
    ("kids", "Can kids do the bio bay tour?", r"\bkids?\b|child|toddler|family|age limit"),
    ("book", "Which bio bay tour company should I book?", r"which (tour|company)|recommend.*(tour|company)|book with|operator"),
]


def intent_of(text):
    t = text.lower()
    for key, canon, pat in INTENTS:
        if re.search(pat, t):
            return key, canon
    toks = [w for w in re.findall(r"[a-z']+", t) if w not in STOP and len(w) > 2]
    k = "misc:" + " ".join(sorted(set(toks))[:4])
    return k, text.strip()


def score(rows):
    now = datetime.now(timezone.utc)
    s = 0.0
    for r in rows:
        try:
            age_d = max(0.0, (now - datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))).total_seconds() / 86400)
        except Exception:
            age_d = 180
        recency = math.exp(-age_d / 120)  # ~4-month half-life-ish decay
        s += (1 + math.log1p(r.get("likes", 0))) * (0.4 + 0.6 * recency)
    srcs = {r["source"] for r in rows}
    return round(s * (1 + 0.25 * (len(srcs) - 1)), 2)  # cross-source agreement bonus


def rank(rows, top):
    clusters = {}
    for r in rows:
        k, canon = intent_of(r["text"])
        c = clusters.setdefault(k, {"intent": k, "question": canon, "rows": []})
        c["rows"].append(r)
    out = []
    for c in clusters.values():
        c["score"] = score(c["rows"])
        c["mentions"] = len(c["rows"])
        c["sources"] = sorted({r["source"] for r in c["rows"]})
        c["examples"] = [r["text"] for r in sorted(c["rows"], key=lambda r: -r.get("likes", 0))[:3]]
        del c["rows"]
        out.append(c)
    out.sort(key=lambda c: (-c["score"], -c["mentions"]))
    return out[:top]


def save_raw(rows):
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = sqlite3.connect(DB)
    con.execute("create table if not exists questions(source, ref, text, likes int, ts, collected_at, unique(source, ref, text))")
    now = datetime.now(timezone.utc).isoformat()
    con.executemany("insert or ignore into questions values(?,?,?,?,?,?)", [(r["source"], r.get("ref", ""), r["text"], r.get("likes", 0), r.get("ts", now), now) for r in rows])
    con.commit(); con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default="youtube,reddit,suggest,seed")
    ap.add_argument("--top", type=int, default=20)
    a = ap.parse_args()
    log = lambda m: print(f"[collect] {m}", file=sys.stderr)
    rows = []
    for name in a.sources.split(","):
        try:
            rows += SOURCES[name](log)
        except Exception as e:
            log(f"{name}: ERROR {e}")
    if not rows:
        log("0 rows from all sources — nothing ranked"); sys.exit(1)
    save_raw(rows)
    ranked = rank(rows, a.top)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"generated": datetime.now(timezone.utc).isoformat(), "total_rows": len(rows), "top": ranked}, open(OUT, "w"), indent=2)
    for i, c in enumerate(ranked, 1):
        print(f"{i:>2}. [{c['score']:>6}] {c['question']}  ({c['mentions']}x, {'+'.join(c['sources'])})")


if __name__ == "__main__":
    main()
