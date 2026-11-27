"""Clip generator — turns a shot list into vertical video clips with the Kling API.

  python3 clips/kling.py clips/shots/01-can-you-swim-in-it.json --out out/

Provider (first one found wins):
  FAL_KEY                               -> fal.ai queue API, Kling 3.0 Turbo Standard (pay as you go)
  KLING_API_KEY                         -> Kling direct, Authorization: Bearer <key>
  KLING_ACCESS_KEY + KLING_SECRET_KEY   -> Kling direct, short-lived HS256 JWT (iss=access key)
Spend guards:
  - a clip whose file already exists in --out is skipped, never regenerated
  - a started task is remembered (Kling: external_task_id; fal: <out>/_state/<list>.json) and
    resumed on the next run, not re-bought
  - MAX_CLIPS (default 6) caps new paid tasks per run
Every step is logged to <out>/_log/<list>.log, including API error bodies, because the run happens
in GitHub Actions and the log file is the only thing that comes back.
"""
import argparse, base64, hashlib, hmac, json, os, sys, time, urllib.error, urllib.request

BASE = os.environ.get("KLING_BASE", "https://api-singapore.klingai.com")
MODEL_PATH = "/text-to-video/kling-3.0"
FAL_MODEL = os.environ.get("FAL_MODEL", "fal-ai/kling-video/v3/turbo/standard/text-to-video")
FAL_QUEUE = "https://queue.fal.run/"
MAX_CLIPS = int(os.environ.get("MAX_CLIPS", "6"))
POLL_EVERY, POLL_MAX = 15, 60 * 20
LOG = None


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, file=sys.stderr)
    if LOG:
        LOG.write(line + "\n"); LOG.flush()


def b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def token():
    key = os.environ.get("KLING_API_KEY", "").strip()
    if key:
        return key, "api-key"
    ak, sk = os.environ.get("KLING_ACCESS_KEY", "").strip(), os.environ.get("KLING_SECRET_KEY", "").strip()
    if ak and sk:
        now = int(time.time())
        head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
        body = b64(json.dumps({"iss": ak, "exp": now + 1800, "nbf": now - 5}, separators=(",", ":")).encode())
        sig = b64(hmac.new(sk.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest())
        return f"{head}.{body}.{sig}", "access+secret jwt"
    return None, None


def call(method, path, body=None):
    tok, _ = token()
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:500]}


def first_task(resp):
    d = resp.get("data")
    if isinstance(d, dict) and "result" in d:
        d = d["result"]
    if isinstance(d, list):
        return d[0] if d else None
    return d if isinstance(d, dict) and d.get("id") else None


def video_url(task):
    for o in task.get("outputs") or []:
        if o.get("type") == "video" and o.get("url"):
            return o["url"]
    return None


def fal_call(method, url, body=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Key {os.environ['FAL_KEY'].strip()}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:500]}


def save(url, dest):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with urllib.request.urlopen(url, timeout=180) as r, open(dest, "wb") as f:
        f.write(r.read())


def make_fal(shot, list_id, style, out_dir):
    dest = os.path.join(out_dir, list_id, f"{shot['id']}.mp4")
    if os.path.exists(dest):
        log(f"{shot['id']}: SKIP — already have {dest}"); return "have"
    prompt = f"{shot['prompt']} {style}".strip()
    key = f"{shot['id']}-{hashlib.sha1((FAL_MODEL + prompt).encode()).hexdigest()[:8]}"
    spath = os.path.join(out_dir, "_state", f"{list_id}.json")
    state = json.load(open(spath)) if os.path.exists(spath) else {}
    job = state.get(key)
    if job:
        log(f"{shot['id']}: resuming request {job['request_id']}")
    else:
        if make.bought >= MAX_CLIPS:
            log(f"{shot['id']}: NOT STARTED — MAX_CLIPS={MAX_CLIPS} reached this run"); return "capped"
        code, resp = fal_call("POST", FAL_QUEUE + FAL_MODEL, {"prompt": prompt, "duration": str(int(shot.get("seconds", 5))), "aspect_ratio": "9:16"})
        if code not in (200, 201, 202) or not resp.get("request_id") or not resp.get("status_url") or not resp.get("response_url"):
            log(f"{shot['id']}: CREATE FAILED http={code} body={json.dumps(resp)[:600]}"); return "failed"
        job = {k: resp[k] for k in ("request_id", "status_url", "response_url")}
        state[key] = job; make.bought += 1
        os.makedirs(os.path.dirname(spath), exist_ok=True); json.dump(state, open(spath, "w"), indent=1)
        log(f"{shot['id']}: request {job['request_id']} queued")
    t0 = time.time()
    while True:
        code, st = fal_call("GET", job["status_url"])
        status = st.get("status") if code in (200, 202) else None
        if status == "COMPLETED":
            break
        if status not in ("IN_QUEUE", "IN_PROGRESS"):
            log(f"{shot['id']}: STATUS http={code} body={json.dumps(st)[:400]}")
            if code in (404, 422) or status:  # gone or failed for good: forget it so a rerun can retry
                state.pop(key, None); json.dump(state, open(spath, "w"), indent=1); return "failed"
        if time.time() - t0 > POLL_MAX:
            log(f"{shot['id']}: TIMED OUT after {POLL_MAX}s; request kept, next run resumes it"); return "timeout"
        time.sleep(POLL_EVERY)
    code, res = fal_call("GET", job["response_url"])
    url = (res.get("video") or {}).get("url") if code == 200 else None
    if not url:
        log(f"{shot['id']}: RESULT http={code} body={json.dumps(res)[:600]}")
        state.pop(key, None); json.dump(state, open(spath, "w"), indent=1); return "failed"
    save(url, dest)
    log(f"{shot['id']}: saved {dest} ({os.path.getsize(dest) // 1024} KB, {int(time.time() - t0)}s)")
    return "new"


def make(shot, list_id, style, out_dir):
    dest = os.path.join(out_dir, list_id, f"{shot['id']}.mp4")
    if os.path.exists(dest):
        log(f"{shot['id']}: SKIP — already have {dest}"); return "have"
    prompt = f"{shot['prompt']} {style}".strip()
    ext = f"{list_id}-{shot['id']}-{hashlib.sha1(prompt.encode()).hexdigest()[:8]}"
    code, resp = call("GET", f"/tasks?external_task_ids={ext}")
    task = first_task(resp) if code == 200 else None
    if task:
        log(f"{shot['id']}: reusing existing task {task.get('id')} ({task.get('status')})")
    else:
        if make.bought >= MAX_CLIPS:
            log(f"{shot['id']}: NOT STARTED — MAX_CLIPS={MAX_CLIPS} reached this run"); return "capped"
        code, resp = call("POST", MODEL_PATH, {
            "prompt": prompt,
            "settings": {"resolution": "720p", "aspect_ratio": "9:16", "duration": int(shot.get("seconds", 5)), "audio": "off", "multi_shot": False},
            "options": {"external_task_id": ext, "watermark_info": {"enabled": False}},
        })
        task = first_task(resp)
        if code != 200 or not task:
            log(f"{shot['id']}: CREATE FAILED http={code} body={json.dumps(resp)[:600]}"); return "failed"
        make.bought += 1
        log(f"{shot['id']}: task {task['id']} created ({task.get('status')})")
    t0 = time.time()
    while task.get("status") in ("submitted", "processing"):
        if time.time() - t0 > POLL_MAX:
            log(f"{shot['id']}: TIMED OUT after {POLL_MAX}s, task {task.get('id')} still {task.get('status')}"); return "timeout"
        time.sleep(POLL_EVERY)
        code, resp = call("GET", f"/tasks?task_ids={task['id']}")
        nxt = first_task(resp) if code == 200 else None
        if not nxt:
            log(f"{shot['id']}: poll http={code} body={json.dumps(resp)[:300]}"); continue
        task = nxt
    url = video_url(task)
    if task.get("status") != "succeeded" or not url:
        log(f"{shot['id']}: TASK {task.get('status')} — {json.dumps(task)[:600]}"); return "failed"
    save(url, dest)
    log(f"{shot['id']}: saved {dest} ({os.path.getsize(dest) // 1024} KB, {int(time.time() - t0)}s)")
    return "new"


make.bought = 0


def main():
    global LOG
    ap = argparse.ArgumentParser()
    ap.add_argument("shots"); ap.add_argument("--out", default="out")
    a = ap.parse_args()
    spec = json.load(open(a.shots, encoding="utf-8"))
    list_id = spec["id"]
    os.makedirs(os.path.join(a.out, "_log"), exist_ok=True)
    LOG = open(os.path.join(a.out, "_log", f"{list_id}.log"), "a", encoding="utf-8")
    if os.environ.get("FAL_KEY", "").strip():
        log(f"=== {list_id}: {len(spec['shots'])} shots, provider=fal, model={FAL_MODEL}")
        fn = make_fal
    else:
        log(f"=== {list_id}: {len(spec['shots'])} shots, provider=kling-direct, base={BASE}, model={MODEL_PATH}")
        tok, kind = token()
        if not tok:
            log("NO KEYS — set FAL_KEY (or KLING_API_KEY, or KLING_ACCESS_KEY + KLING_SECRET_KEY) as a repo secret. Nothing generated."); sys.exit(2)
        log(f"auth: {kind}")
        fn = make
    results = {s["id"]: fn(s, list_id, spec.get("style", ""), a.out) for s in spec["shots"]}
    log(f"=== result: {json.dumps(results)}  new paid tasks this run: {make.bought}")
    sys.exit(0 if all(v in ("new", "have") for v in results.values()) else 1)


if __name__ == "__main__":
    main()
