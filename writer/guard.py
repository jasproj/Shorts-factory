"""Script guard — turns a hand-written or generated batch (.md) into the JSON queue the renderer reads,
and rejects any script that breaks the rules. Nothing reaches render without passing here.

Rules (a failure names the script and the reason; exit 1 if any script fails):
  facts    every fact ID cited exists in facts/biobay.md, and at least one is cited
  length   VO is 28-52 words (about 12-18 seconds)
  price    no dollar amounts in bubble or VO (prices come live from FareHarbor, never typed)
  shape    bubble, VO, highlights and link are all present
  stale    WARN only: fact sheet verified more than 90 days ago

Usage: python3 writer/guard.py scripts/test-batch-01.md [--out scripts/batch-01.json]
"""
import argparse, json, os, re, sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FACTS = os.path.join(ROOT, "facts", "biobay.md")
MIN_WORDS, MAX_WORDS = 28, 52


def load_facts(path=FACTS):
    txt = open(path, encoding="utf-8").read()
    ids = set(re.findall(r"^\|\s*(F\d{2})\s*\|", txt, re.M))
    m = re.search(r"Verified (\d{4})-(\d{2})-(\d{2})", txt)
    verified = date(*map(int, m.groups())) if m else None
    return ids, verified


def field(block, name):
    m = re.search(rf"^\*\*{name}:\*\*\s*(.+)$", block, re.M)
    return m.group(1).strip() if m else ""


def parse(md):
    out = []
    for m in re.finditer(r"^### (\d+) — (.+?)\n(.*?)(?=^### |^---|\Z)", md, re.M | re.S):
        num, title, block = m.group(1), m.group(2).strip(), m.group(3)
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        out.append({
            "id": f"{num}-{slug}",
            "title": title,
            "bubble": field(block, "Bubble").strip('"'),
            "vo": field(block, "VO").strip('"'),
            "highlights": [h.strip() for h in field(block, "Hi").split("·") if h.strip()],
            "facts": re.findall(r"F\d{2}", field(block, "Facts")),
            "link": field(block, "Link"),
        })
    return out


def check(s, fact_ids):
    errs = []
    for k in ("bubble", "vo", "highlights", "link"):
        if not s[k]:
            errs.append(f"shape: missing {k}")
    if not s["facts"]:
        errs.append("facts: none cited")
    for f in s["facts"]:
        if f not in fact_ids:
            errs.append(f"facts: {f} is not in the fact sheet")
    n = len(s["vo"].split())
    if s["vo"] and not (MIN_WORDS <= n <= MAX_WORDS):
        errs.append(f"length: VO is {n} words (allowed {MIN_WORDS}-{MAX_WORDS})")
    if re.search(r"\$\s?\d|\d+\s?(dollars|bucks|usd)\b", s["bubble"] + " " + s["vo"], re.I):
        errs.append("price: dollar amount typed into the script")
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("batch")
    ap.add_argument("--out")
    a = ap.parse_args()
    fact_ids, verified = load_facts()
    if not fact_ids:
        print("[guard] 0 fact IDs parsed from the fact sheet — refusing to pass anything", file=sys.stderr); sys.exit(1)
    scripts = parse(open(a.batch, encoding="utf-8").read())
    if not scripts:
        print(f"[guard] 0 scripts parsed from {a.batch} — wrong format?", file=sys.stderr); sys.exit(1)
    if verified and (date.today() - verified).days > 90:
        print(f"[guard] WARN fact sheet verified {verified}, over 90 days ago — re-verify", file=sys.stderr)
    bad = 0
    for s in scripts:
        errs = check(s, fact_ids)
        s["words"] = len(s["vo"].split())
        print(f"{'FAIL' if errs else 'ok  '} {s['id']}  ({s['words']} words, {' '.join(s['facts'])})")
        for e in errs:
            print(f"       {e}")
        bad += bool(errs)
    print(f"[guard] {len(scripts) - bad}/{len(scripts)} passed, {len(fact_ids)} facts on the sheet", file=sys.stderr)
    if bad:
        sys.exit(1)
    if a.out:
        json.dump(scripts, open(a.out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        print(f"[guard] wrote {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
