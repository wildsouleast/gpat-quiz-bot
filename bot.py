# GPAT / B.Pharm 3rd-sem quiz bot (Telegram) — "GPAT Quiz by Anees". Webhook mode for Render free.
# Env: TELEGRAM_BOT_TOKEN (required), OWNER_CHAT_ID (backup + /stats), CRON_KEY (daily quiz trigger),
#      PORT and RENDER_EXTERNAL_URL (set by Render) or PUBLIC_URL.
import os, sys, json, time, random, hashlib, threading, signal, traceback, pathlib, datetime
import requests
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
if not TOKEN:
    print("TELEGRAM_BOT_TOKEN missing", flush=True); sys.exit(1)
OWNER = (os.environ.get("OWNER_CHAT_ID") or "").strip()
CRON_KEY = (os.environ.get("CRON_KEY") or "").strip()
API = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")
BASE = f"{API}/bot{TOKEN}"
PUBLIC = (os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")
SECRET = hashlib.sha256(("gpatquiz:" + TOKEN).encode()).hexdigest()[:48]
HOOK_PATH = "/tg/" + SECRET[:24]
DATA = pathlib.Path(os.environ.get("QUIZ_DATA", "/tmp/gpat_quiz_state.json"))
CTA = "Poora 30-day exam plan + sab answers → https://pharma3sem.publicvm.com (₹49)"
QUIZ_LEN = 10
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
BACKUP_TAG = "gpatquiz-state-v1"

BANK = json.load(open(pathlib.Path(__file__).with_name("questions.json"), encoding="utf-8"))
QS = BANK["questions"]
SUBJ = BANK["subjects"]
BY_SUB = {}
for i, q in enumerate(QS):
    BY_SUB.setdefault(q["subject"], []).append(i)

# ---------------- telegram ----------------
def tg(method, _files=None, **params):
    try:
        if _files:
            data = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in params.items()}
            r = requests.post(f"{BASE}/{method}", data=data, files=_files, timeout=40)
        else:
            r = requests.post(f"{BASE}/{method}", json=params, timeout=40)
        j = r.json()
        if not j.get("ok"):
            print("tg", method, j.get("error_code"), j.get("description"), flush=True)
        return j
    except Exception as e:
        print("tg error", method, type(e).__name__, flush=True)
        return {"ok": False}

def say(chat, text, **kw):
    return tg("sendMessage", chat_id=chat, text=text, disable_web_page_preview=True, **kw)

# ---------------- state (local file + pinned backup in the owner's chat) ----------------
lock = threading.RLock()
db = {"users": {}, "polls": {}, "daily_sent": "", "backup_msg": None}
dirty = False

def now_ist():
    return datetime.datetime.now(IST)

def load_state():
    global db
    try:
        db.update(json.loads(DATA.read_text())); print("state: local file", flush=True); return
    except Exception:
        pass
    if not OWNER:
        return
    try:
        ch = tg("getChat", chat_id=OWNER).get("result") or {}
        pm = ch.get("pinned_message") or {}
        doc = pm.get("document") or {}
        if BACKUP_TAG not in (pm.get("caption") or "") or not doc:
            return
        f = tg("getFile", file_id=doc["file_id"]).get("result") or {}
        raw = requests.get(f"{API}/file/bot{TOKEN}/{f['file_path']}", timeout=40).content
        db.update(json.loads(raw)); db["backup_msg"] = pm.get("message_id")
        print("state: restored from pinned backup,", len(db["users"]), "users", flush=True)
    except Exception as e:
        print("restore failed", type(e).__name__, flush=True)

def mark():
    global dirty
    dirty = True

def save_state(remote=True):
    global dirty
    with lock:
        # keep polls map small: only open polls of the last 2 days
        cut = time.time() - 2 * 86400
        db["polls"] = {k: v for k, v in db["polls"].items() if v.get("t", 0) > cut}
        blob = json.dumps(db, ensure_ascii=False).encode()
        dirty = False
    try:
        DATA.write_bytes(blob)
    except Exception:
        pass
    if not (remote and OWNER):
        return
    cap = f"{BACKUP_TAG} · {len(db['users'])} users · {now_ist():%d %b %H:%M} IST (bot backup — delete mat karna)"
    files = {"document": ("quiz_state.json", blob, "application/json")}
    mid = db.get("backup_msg")
    if mid:
        r = tg("editMessageMedia", _files=files, chat_id=OWNER, message_id=mid,
               media={"type": "document", "media": "attach://document", "caption": cap})
        if r.get("ok") or "not modified" in str(r.get("description", "")):
            return
    r = tg("sendDocument", _files=files, chat_id=OWNER, caption=cap, disable_notification=True)
    if r.get("ok"):
        db["backup_msg"] = r["result"]["message_id"]
        tg("pinChatMessage", chat_id=OWNER, message_id=db["backup_msg"], disable_notification=True)

def saver():
    while True:
        time.sleep(90)
        if dirty:
            try: save_state()
            except Exception: traceback.print_exc()

def user(uid, frm=None):
    u = db["users"].setdefault(str(uid), {"name": "", "daily": False, "answered": 0, "correct": 0,
                                           "quizzes": 0, "best": 0, "sess": None, "first": int(time.time())})
    if frm:
        u["name"] = (frm.get("first_name") or "")[:40]
    return u

# ---------------- quiz flow ----------------
def pick(subject=None, n=QUIZ_LEN):
    pool = BY_SUB.get(subject) if subject else list(range(len(QS)))
    return random.sample(pool, min(n, len(pool)))

def start_quiz(chat, subject=None, title=None):
    with lock:
        u = user(chat)
        u["sess"] = {"q": pick(subject), "i": 0, "ok": 0, "sub": subject}
        mark()
    head = title or (f"📚 {SUBJ[subject]} — {QUIZ_LEN} sawaal" if subject else f"📝 Mixed GPAT quiz — {QUIZ_LEN} sawaal")
    say(chat, head + "\nHar sawaal ka sahi option chuno. Chalo shuru! 💪")
    send_next(chat)

def send_next(chat):
    with lock:
        u = user(chat); s = u.get("sess")
        if not s:
            return
        if s["i"] >= len(s["q"]):
            finish(chat, u, s); return
        q = QS[s["q"][s["i"]]]
        opts = [q["correct"]] + q["wrong"]
        random.shuffle(opts)
        ans = opts.index(q["correct"])
        num = s["i"] + 1
    r = tg("sendPoll", chat_id=chat, question=f"Q{num}/{len(s['q'])} [{SUBJ[q['subject']]}] {q['q']}"[:300],
           options=[{"text": o} for o in opts], type="quiz", correct_option_id=ans, is_anonymous=False,
           explanation=q["explain"][:200])
    if r.get("ok"):
        pid = r["result"]["poll"]["id"]
        with lock:
            db["polls"][pid] = {"c": str(chat), "a": ans, "n": num, "t": int(time.time())}
            mark()

def finish(chat, u, s):
    tot, ok = len(s["q"]), s["ok"]
    u["sess"] = None; u["quizzes"] += 1; u["best"] = max(u["best"], ok); mark()
    msg = "🔥 Zabardast!" if ok >= 8 else ("👍 Achha hai, thoda aur practice!" if ok >= 5 else "💡 Koi baat nahi, roz practice se score badhega!")
    say(chat, f"✅ Quiz khatam! Score: {ok}/{tot}\n{msg}\n\nPhir se: /quiz · Subject choose: /subject · Total score: /score\n\n{CTA}")

def on_poll_answer(pa):
    pid = pa.get("poll_id"); chosen = pa.get("option_ids") or []
    frm = pa.get("user") or {}
    with lock:
        p = db["polls"].pop(pid, None)
        if not p or not chosen:
            return
        chat = p["c"]
        if str(frm.get("id")) != chat:
            return
        u = user(chat, frm)
        good = chosen[0] == p["a"]
        u["answered"] += 1; u["correct"] += int(good)
        s = u.get("sess")
        if s and s["i"] + 1 == p["n"]:
            s["ok"] += int(good); s["i"] += 1
            advance = True
        else:
            advance = False
        mark()
    if advance:
        time.sleep(1.2)   # let them read the explanation
        send_next(chat)

SUB_ORDER = ["pharmaceutics", "chemistry", "physpharm", "micro", "engg", "pharmacology"]

def subject_keyboard():
    rows = [[{"text": SUBJ[k], "callback_data": "sub:" + k}] for k in SUB_ORDER if k in SUBJ]
    rows.append([{"text": "🎲 Mixed (sab subject)", "callback_data": "sub:mix"}])
    return {"inline_keyboard": rows}

WELCOME = ("Namaste {name}! 👋 Main hoon *GPAT Quiz by Anees* 🧪\n\n"
           "B.Pharm 3rd sem + GPAT ke MCQ roz practice karo — sahi answer + 1 line explanation ke saath.\n\n"
           "/quiz — 10 random MCQ\n/subject — subject choose karo\n/daily — roz 8 PM IST pe 10 sawaal\n/score — apna score dekho\n\n"
           "Chalo /quiz se shuru karo! 💪")

def on_message(m):
    chat = m["chat"]["id"]
    if m["chat"].get("type") != "private":
        return
    text = (m.get("text") or "").strip()
    cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
    with lock:
        u = user(chat, m.get("from")); mark()
    if cmd in ("/start", "/help"):
        say(chat, WELCOME.replace("{name}", u["name"] or "dost"), parse_mode="Markdown")
    elif cmd == "/quiz":
        start_quiz(chat)
    elif cmd == "/subject":
        tg("sendMessage", chat_id=chat, text="Kaunsa subject? 👇", reply_markup=subject_keyboard())
    elif cmd == "/daily":
        with lock:
            u["daily"] = not u["daily"]; on = u["daily"]; mark()
        save_async()
        say(chat, "✅ Daily quiz ON — roz raat 8 baje (IST) 10 naye sawaal aayenge. Band karne ke liye phir /daily bhejo." if on
            else "🔕 Daily quiz OFF. Wapas chalu karna ho to /daily bhejo.")
    elif cmd == "/score":
        a, c = u["answered"], u["correct"]
        pct = round(100 * c / a) if a else 0
        say(chat, f"📊 Tumhara score\nSawaal attempt: {a}\nSahi: {c} ({pct}%)\nQuiz poore kiye: {u['quizzes']}\nBest quiz: {u['best']}/{QUIZ_LEN}\n"
                  f"Daily quiz: {'ON ✅' if u['daily'] else 'OFF'}\n\n{CTA}")
    elif cmd == "/stats" and OWNER and str(chat) == OWNER:
        us = db["users"].values()
        say(chat, f"Users: {len(db['users'])}\nDaily ON: {sum(1 for x in us if x['daily'])}\nAnswers: {sum(x['answered'] for x in us)}\nQuestions in bank: {len(QS)}")
    else:
        say(chat, "Quiz ke liye /quiz bhejo, subject choose karne ke liye /subject 🙂")

def on_callback(cq):
    data = cq.get("data") or ""; chat = (cq.get("message") or {}).get("chat", {}).get("id")
    tg("answerCallbackQuery", callback_query_id=cq["id"])
    if not chat or not data.startswith("sub:"):
        return
    k = data[4:]
    start_quiz(chat, None if k == "mix" else (k if k in SUBJ else None))

def run_daily(force=False):
    today = now_ist().strftime("%Y-%m-%d")
    with lock:
        if db.get("daily_sent") == today and not force:
            return 0
        db["daily_sent"] = today; mark()
        subs = [cid for cid, u in db["users"].items() if u.get("daily")]
    n = 0
    for cid in subs:
        start_quiz(int(cid), None, title=f"🌙 Aaj ka Daily GPAT Quiz ({now_ist():%d %b}) — {QUIZ_LEN} sawaal")
        n += 1; time.sleep(0.2)
    save_async()
    print("daily sent to", n, flush=True)
    return n

def save_async():
    threading.Thread(target=save_state, daemon=True).start()

_seen = []
def process_update(upd):
    uid = upd.get("update_id")
    if uid in _seen:
        return
    _seen.append(uid); del _seen[:-500]
    try:
        if "message" in upd: on_message(upd["message"])
        elif "poll_answer" in upd: on_poll_answer(upd["poll_answer"])
        elif "callback_query" in upd: on_callback(upd["callback_query"])
    except Exception:
        traceback.print_exc()

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body=b"ok"):
        self.send_response(code); self.send_header("Content-Type", "text/plain"); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if self.path in ("/", "/ping", "/health"):
            self._send(200, f"gpat quiz bot ok · {len(QS)} questions".encode())
        else:
            self._send(404, b"no")
    do_HEAD = do_GET
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0); raw = self.rfile.read(n) if n else b""
        if self.path == HOOK_PATH:
            if self.headers.get("X-Telegram-Bot-Api-Secret-Token") != SECRET:
                return self._send(403, b"no")
            self._send(200)
            try: upd = json.loads(raw)
            except Exception: return
            threading.Thread(target=process_update, args=(upd,), daemon=True).start()
        elif self.path.startswith("/cron/daily"):
            if not CRON_KEY or self.headers.get("X-Cron-Key") != CRON_KEY:
                return self._send(403, b"no")
            hhmm = now_ist().strftime("%H%M")
            if hhmm < "1955" and "force" not in self.path:
                return self._send(200, f"too early ({hhmm} IST)".encode())
            threading.Thread(target=run_daily, args=("force" in self.path,), daemon=True).start()
            self._send(200, b"daily started")
        else:
            self._send(404, b"no")

def register_webhook():
    if not PUBLIC:
        print("no PUBLIC_URL; webhook not set", flush=True); return
    for _ in range(5):
        r = tg("setWebhook", url=PUBLIC + HOOK_PATH, secret_token=SECRET,
               allowed_updates=["message", "poll_answer", "callback_query"], max_connections=20)
        if r.get("ok"):
            print("webhook set", flush=True); break
        time.sleep(3)
    tg("setMyCommands", commands=[
        {"command": "quiz", "description": "10 random MCQ"},
        {"command": "subject", "description": "Subject choose karo"},
        {"command": "daily", "description": "Roz 8 PM IST quiz (on/off)"},
        {"command": "score", "description": "Apna score"},
        {"command": "start", "description": "Shuru / help"}])
    tg("setMyDescription", description="B.Pharm 3rd sem + GPAT MCQ quiz, roz free. Sahi answer + explanation ke saath. By Anees Reddy.")
    tg("setMyShortDescription", short_description="Free daily GPAT / B.Pharm MCQ quiz — by Anees Reddy")

def webhook_guard():
    while True:
        time.sleep(600)
        if not PUBLIC: continue
        info = tg("getWebhookInfo").get("result") or {}
        if info.get("url") != PUBLIC + HOOK_PATH:
            print("webhook drifted; re-registering", flush=True); register_webhook()

def on_term(*_):
    try: save_state()
    finally: os._exit(0)

def main():
    load_state()
    signal.signal(signal.SIGTERM, on_term)
    port = int(os.environ.get("PORT", "10000"))
    srv = ThreadingHTTPServer(("0.0.0.0", port), H)
    threading.Thread(target=register_webhook, daemon=True).start()
    threading.Thread(target=webhook_guard, daemon=True).start()
    threading.Thread(target=saver, daemon=True).start()
    # catch-up: woke up after 8 PM and today's daily not sent yet
    def catchup():
        time.sleep(20)
        if now_ist().strftime("%H%M") >= "2000" and db.get("daily_sent") != now_ist().strftime("%Y-%m-%d"):
            run_daily()
    threading.Thread(target=catchup, daemon=True).start()
    print("listening on", port, "questions", len(QS), flush=True)
    srv.serve_forever()

if __name__ == "__main__":
    main()
