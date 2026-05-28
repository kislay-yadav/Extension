"""
AroLink Catcher Backend — Clean Final Version
- Sends to @Nick_Bypass_Bot privately
- Matches reply STRICTLY by reply_to_msg_id (your msg only)
- Extracts ONLY the Bypassed Link (not Original Link)
- Multiple users get their own correct response
- Customizable delay via extension settings
- Auto-ping to prevent Render sleep
- No feedback endpoint
"""

import os, asyncio, re, uuid, logging, httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from telethon import TelegramClient, events
from telethon.sessions import StringSession

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("arocatcher")

# ── Config ────────────────────────────────────────────────────────
API_ID       = int(os.environ.get("TG_API_ID", "21952127"))
API_HASH     = os.environ.get("TG_API_HASH", "e0a3741bb3b132947d86d8fc6218eebe")
SESSION_STR  = os.environ.get("TG_SESSION", "")
SECRET_KEY   = os.environ.get("SECRET_KEY", "changeme")
RENDER_URL   = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
BOT_USER_ID  = int(os.environ.get("BOT_USER_ID", "8226002644"))
BOT_USERNAME = os.environ.get("BYPASS_BOT", "@Nick_Bypass_Bot")

# ── State ─────────────────────────────────────────────────────────
# req_id → {"status": "pending"|"done"|"error", "url": str}
results   : dict = {}
# req_id → sent message id — STRICTLY matched to get correct reply
sent_msgs : dict = {}
client    : TelegramClient | None = None

# ── Extract ONLY the bypassed URL (not the original link) ─────────
def get_bypassed_url(text: str) -> str:
    """
    Bot reply format:
      Original Link : ✅ https://arolinks.com/xyz     ← we DON'T want this
      Bypassed Link: ✅ https://final-destination...  ← we WANT this

    Strategy: find URL that comes AFTER 'Bypassed Link' keyword.
    """
    # Method 1: URL strictly after "Bypassed Link" keyword
    m = re.search(
        r'[Bb]ypassed\s*[Ll]ink[^h]*(https?://\S+)',
        text, re.DOTALL
    )
    if m:
        url = m.group(1).rstrip('* \n.,);\'\"')
        # Make sure it's not the arolinks original link
        if url and 'arolinks.com' not in url and 'mahnokari.com' not in url:
            log.info(f"Bypassed URL (keyword match): {url[:80]}")
            return url

    # Method 2: Collect ALL urls, skip arolinks/mahnokari, return LAST one
    # Bot always puts bypassed link AFTER original link, so last = bypassed
    all_urls = re.findall(r'https?://[^\s\*\n\)\]\"\']+', text)
    candidates = []
    for u in all_urls:
        u = u.rstrip('* \n.,);\'\"')
        if (u and
            'arolinks.com' not in u and
            'mahnokari.com' not in u and
            'telegram.org' not in u and
            't.me' not in u and
            'google.com' not in u):
            candidates.append(u)

    if candidates:
        log.info(f"Bypassed URL (last URL): {candidates[-1][:80]}")
        return candidates[-1]

    return ""

# ── Handle bot reply ──────────────────────────────────────────────
async def handle_msg(event):
    try:
        text     = event.message.text or event.message.message or ""
        msg_id   = event.message.id
        reply_to = event.message.reply_to_msg_id or 0
        from_id  = getattr(event.message.from_id, 'user_id', 0)

        log.info(f"📨 msg={msg_id} from={from_id} reply_to={reply_to}")
        log.info(f"   text={text[:150]}")

        # Only process messages from Nick Bypass Bot
        if from_id != BOT_USER_ID:
            return

        # Skip "Processing..." interim messages
        stripped = text.strip().replace('*', '').replace('_', '')
        if stripped.lower().startswith('processing') and len(stripped) < 40:
            log.info("Skip: Processing message")
            return

        # No pending requests — nothing to do
        if not sent_msgs:
            log.info("No pending requests")
            return

        # ── STRICT MATCH by reply_to_msg_id ──────────────────────
        # This is the ONLY reliable way when multiple people use the bot.
        # The bot replies to YOUR message → reply_to == your sent message id.
        # Other people's replies have different reply_to values → ignored.
        matched_req = None
        for req_id, our_sent_id in list(sent_msgs.items()):
            if reply_to == our_sent_id:
                matched_req = req_id
                log.info(f"✅ Strict match: reply_to={reply_to} == sent={our_sent_id}")
                break

        if not matched_req:
            log.info(f"⚠️ reply_to={reply_to} doesn't match any of ours {list(sent_msgs.values())} — ignoring")
            return

        # Check request is still pending
        r = results.get(matched_req)
        if not r or r["status"] != "pending":
            log.info(f"Request {matched_req} already resolved")
            return

        # Extract the bypassed URL
        result_url = get_bypassed_url(text)
        if not result_url:
            log.info(f"Could not extract bypassed URL from: {text[:100]}")
            return

        # Resolve
        results[matched_req] = {"status": "done", "url": result_url}
        log.info(f"✅ Resolved {matched_req} → {result_url[:80]}")

    except Exception as e:
        log.error(f"handle_msg error: {e}", exc_info=True)

# ── Setup Telethon handlers ───────────────────────────────────────
def setup_handlers(c: TelegramClient):
    # Primary: listen specifically from the bot
    @c.on(events.NewMessage(incoming=True, from_users=BOT_USERNAME))
    async def on_bot_direct(event):
        await handle_msg(event)

    # Backup: catch any incoming from bot user_id
    @c.on(events.NewMessage(incoming=True))
    async def on_incoming(event):
        try:
            from_id = getattr(event.message.from_id, 'user_id', 0)
            if from_id == BOT_USER_ID:
                await handle_msg(event)
        except Exception as e:
            log.error(f"on_incoming: {e}")

# ── Auto-ping to prevent Render sleep ────────────────────────────
async def auto_ping():
    await asyncio.sleep(60)
    while True:
        try:
            url = RENDER_URL or "http://localhost:8000"
            async with httpx.AsyncClient() as hc:
                r = await hc.get(f"{url}/health", timeout=10)
                log.info(f"🏓 Ping {r.status_code}")
        except Exception as e:
            log.warning(f"Ping: {e}")
        await asyncio.sleep(600)

# ── Cleanup old results ───────────────────────────────────────────
async def cleanup_loop():
    while True:
        await asyncio.sleep(300)
        done_keys = [k for k, v in results.items()
                     if v["status"] in ("done", "error")]
        for k in done_keys[:-50]:
            results.pop(k, None)
            sent_msgs.pop(k, None)
        if done_keys:
            log.info(f"🧹 Cleanup: kept {min(50, len(done_keys))} results")

# ── Lifespan ──────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global client
    if not SESSION_STR:
        log.error("❌ TG_SESSION env var not set!")
    else:
        client = TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH)
        setup_handlers(client)
        await client.start()
        try:
            me = await client.get_me()
            log.info(f"✅ Userbot: {me.first_name} (@{me.username}) id={me.id}")
        except Exception as e:
            log.warning(f"get_me warning (non-fatal): {e}")
        log.info(f"🤖 Sending to: {BOT_USERNAME} (uid={BOT_USER_ID})")

    ping_task    = asyncio.create_task(auto_ping())
    cleanup_task = asyncio.create_task(cleanup_loop())
    yield
    ping_task.cancel()
    cleanup_task.cancel()
    if client:
        await client.disconnect()

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

# ── Models ────────────────────────────────────────────────────────
class SubmitReq(BaseModel):
    url: str
    secret: str

# ── POST /submit ──────────────────────────────────────────────────
@app.post("/submit")
async def submit(req: SubmitReq):
    if req.secret != SECRET_KEY:
        raise HTTPException(401, "Invalid secret")
    if not client or not client.is_connected():
        raise HTTPException(503, "Userbot offline — set TG_SESSION in Render env vars")

    req_id = uuid.uuid4().hex[:8]
    results[req_id] = {"status": "pending", "url": None}

    try:
        # Send just the URL — clean, no tags
        sent = await client.send_message(BOT_USERNAME, req.url)
        sent_msgs[req_id] = sent.id
        log.info(f"📤 Sent msg_id={sent.id} to {BOT_USERNAME}: {req.url}")
        return {"ok": True, "req_id": req_id}
    except Exception as e:
        results.pop(req_id, None)
        log.error(f"Send failed: {e}")
        raise HTTPException(500, f"Failed to send to bot: {e}")

# ── GET /result/{req_id} ──────────────────────────────────────────
@app.get("/result/{req_id}")
async def get_result(req_id: str, secret: str = ""):
    if secret != SECRET_KEY:
        raise HTTPException(401, "Invalid secret")
    if req_id not in results:
        return {"status": "not_found"}
    r = results[req_id]
    return {
        "status": r["status"],
        "url":    r.get("url"),
        "error":  r.get("error")
    }

# ── GET /health ───────────────────────────────────────────────────
@app.get("/health")
async def health():
    if client and client.is_connected():
        try:
            me = await client.get_me()
            return {"status": "ok", "userbot": me.first_name,
                    "username": me.username, "bot": BOT_USERNAME}
        except Exception:
            return {"status": "ok", "userbot": "connected",
                    "username": "unknown", "bot": BOT_USERNAME}
    return {"status": "offline", "reason": "TG_SESSION not set or expired"}

# ── GET /debug ────────────────────────────────────────────────────
@app.get("/debug")
async def debug():
    return {
        "pending":   {k: v for k, v in results.items() if v["status"] == "pending"},
        "sent_msgs": dict(sent_msgs),
        "bot":       BOT_USERNAME,
        "connected": client.is_connected() if client else False,
    }

# ── GET / ─────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {
        "service":   "AroLink Catcher",
        "endpoints": ["POST /submit", "GET /result/{req_id}",
                      "GET /health", "GET /debug"]
    }
