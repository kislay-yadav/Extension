"""
AroLink Catcher Backend — v9
Send directly to @Nick_Bypass_Bot (private, fast, no channel noise)
Bot replies directly to userbot in private/saved messages
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
API_ID      = int(os.environ.get("TG_API_ID", "21952127"))
API_HASH    = os.environ.get("TG_API_HASH", "e0a3741bb3b132947d86d8fc6218eebe")
SESSION_STR = os.environ.get("TG_SESSION", "1BVtsOIUBu4dSO-RTLWmhNwNJaRIK3CrDRwhpG3X2fE2aLkQkvAdZjhgfLOU2uFyyFq7Lqx5Vl1_Hr3fasapOBrfsu9i2s7RBbd4Gf66i76oBZ7JvkWsRdemFcZl-d-Q7RoGwTFQvLBLky9tZ3bhA_V1T3IjkhjnXmxdbIBLDTRVd8pCxgEM6Dd2VEjnlyPm6Nnr_8UK5UDVX2j1ulPvnJgn6XgRL7XxMwRsbrdrEO52SF6UqrxaxWipe2uq-kuFLVYkBDkho_rDgt71cJoCT2oE1swVmNHR7s_D06a0sIcJYmsZVzENqHqcPDDNdZVX374Vj3TXHq_G0rj6mDChYtQBsQd5fyFA=")
SECRET_KEY  = os.environ.get("SECRET_KEY", "changeme")
RENDER_URL  = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")

# Send directly to Nick Bypass Bot
BOT_USERNAME = os.environ.get("BYPASS_BOT", "@Nick_Bypass_Bot")

SKIP = ['google.com', 'telegram.org', 'telegra.ph',
        't.me', 'bit.ly', 'youtube.com']

# ── State ─────────────────────────────────────────────────────────
results   : dict = {}  # req_id → {status, url, error}
sent_msgs : dict = {}  # req_id → sent message id
last_code : dict = {}  # req_id → arolink code
last_msgs : list = []  # debug
client    : TelegramClient | None = None

# ── Extract bypassed URL from bot reply ───────────────────────────
def get_bypassed_url(text: str) -> str:
    # Method 1: URL after "Bypassed Link"
    m = re.search(r'[Bb]ypassed\s*[Ll]ink[^h]*(https?://\S+)', text, re.DOTALL)
    if m:
        u = m.group(1).rstrip('* \n.,);\'\"')
        if u and not any(s in u for s in SKIP):
            return u

    # Method 2: last URL in message (bypassed is always last)
    all_urls = re.findall(r'https?://[^\s\*\n\)\]\"\']+', text)
    candidates = []
    for u in all_urls:
        u = u.rstrip('* \n.,);\'\"')
        if u and not any(s in u for s in SKIP):
            candidates.append(u)
    return candidates[-1] if candidates else ""

def resolve_req(req_id: str, url: str, method: str):
    if req_id in results and results[req_id]["status"] == "pending":
        results[req_id] = {"status": "done", "url": url}
        log.info(f"✅ [{method}] {req_id} → {url[:80]}")

# ── Handle bot reply ──────────────────────────────────────────────
async def handle_bot_reply(event):
    try:
        text     = event.message.text or event.message.message or ""
        msg_id   = event.message.id
        reply_to = event.message.reply_to_msg_id or 0
        from_id  = getattr(event.message.from_id, 'user_id', 0)

        log.info(f"📨 msg={msg_id} from={from_id} reply_to={reply_to}")
        log.info(f"   text={text[:150]}")

        last_msgs.append({
            "msg_id": msg_id, "from_id": from_id,
            "reply_to": reply_to, "text": text[:200]
        })
        if len(last_msgs) > 20:
            last_msgs.pop(0)

        # Skip Processing message
        if "Processing" in text and len(text) < 30:
            return

        # Get pending requests
        pending = {k: v for k, v in results.items()
                   if v["status"] == "pending"}
        if not pending:
            return

        # Extract bypassed URL
        result_url = get_bypassed_url(text)
        if not result_url:
            log.info(f"No URL found in: {text[:80]}")
            return

        log.info(f"🎯 URL: {result_url[:80]}")

        matched = False
        for req_id in list(pending.keys()):
            # Match by reply_to_msg_id (most reliable)
            if reply_to and reply_to == sent_msgs.get(req_id):
                resolve_req(req_id, result_url, "msg_id")
                matched = True
                break

            # Match by arolink code in text
            code = last_code.get(req_id, "")
            if code and code in text:
                resolve_req(req_id, result_url, "code")
                matched = True
                break

            # Fallback: only one pending → must be ours
            if len(pending) == 1:
                resolve_req(req_id, result_url, "fallback")
                matched = True
                break

        if not matched:
            log.warning(f"No match. pending={list(pending.keys())} reply_to={reply_to} sent={sent_msgs}")

    except Exception as e:
        log.error(f"handle_bot_reply: {e}", exc_info=True)

# ── Setup handlers — listen to messages FROM the bot ─────────────
def setup_handlers(c: TelegramClient):

    @c.on(events.NewMessage(incoming=True, from_users=BOT_USERNAME))
    async def on_bot_reply(event):
        """Catch replies from @Nick_Bypass_Bot directly to our userbot."""
        await handle_bot_reply(event)

    @c.on(events.NewMessage(incoming=True))
    async def on_any_incoming(event):
        """Backup: catch any incoming message that might be from bot."""
        try:
            from_id = getattr(event.message.from_id, 'user_id', 0)
            # Nick Bypass Bot user ID
            if from_id == 8226002644:
                await handle_bot_reply(event)
        except Exception as e:
            log.error(f"on_any_incoming: {e}")

# ── Auto-ping ─────────────────────────────────────────────────────
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

# ── Lifespan ──────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global client
    client = TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH)
    setup_handlers(client)
    await client.start()
    me = await client.get_me()
    log.info(f"✅ Userbot: {me.first_name} @{me.username}")
    log.info(f"🤖 Sending to bot: {BOT_USERNAME}")
    ping_task = asyncio.create_task(auto_ping())
    yield
    ping_task.cancel()
    await client.disconnect()

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

class SubmitReq(BaseModel):
    url: str
    secret: str

# ── POST /submit ──────────────────────────────────────────────────
@app.post("/submit")
async def submit(req: SubmitReq):
    if req.secret != SECRET_KEY:
        raise HTTPException(401, "Invalid secret")
    if "arolinks.com" not in req.url:
        raise HTTPException(400, "Not an arolinks URL")
    if not client or not client.is_connected():
        raise HTTPException(503, "Userbot offline")

    code   = req.url.rstrip('/').split('/')[-1].split('?')[0]
    req_id = uuid.uuid4().hex[:8]

    results[req_id]   = {"status": "pending", "url": None}
    last_code[req_id] = code

    # Send directly to @Nick_Bypass_Bot
    sent = await client.send_message(BOT_USERNAME, req.url)
    sent_msgs[req_id] = sent.id
    log.info(f"📤 Sent to {BOT_USERNAME} msg_id={sent.id}: {req.url}")

    return {"ok": True, "req_id": req_id, "code": code}

# ── GET /result/:req_id ───────────────────────────────────────────
@app.get("/result/{req_id}")
async def get_result(req_id: str, secret: str = ""):
    if secret != SECRET_KEY:
        raise HTTPException(401, "Invalid secret")
    if req_id not in results:
        return {"status": "not_found"}
    r = results[req_id]
    return {"status": r["status"], "url": r.get("url"), "error": r.get("error")}

# ── GET /health ───────────────────────────────────────────────────
@app.get("/health")
async def health():
    if client and client.is_connected():
        me = await client.get_me()
        return {"status": "ok", "userbot": me.first_name,
                "username": me.username}
    return {"status": "offline"}

# ── GET /debug ────────────────────────────────────────────────────
@app.get("/debug")
async def debug():
    return {
        "pending":     {k: v for k, v in results.items()
                        if v["status"] == "pending"},
        "done_count":  sum(1 for v in results.values()
                           if v["status"] == "done"),
        "bot":         BOT_USERNAME,
        "connected":   client.is_connected() if client else False,
        "last_msgs":   last_msgs[-5:],
    }

# ── GET /test-bot ─────────────────────────────────────────────────
@app.get("/test-bot")
async def test_bot(secret: str = ""):
    """Send test message to bot and read last 3 replies."""
    if secret != SECRET_KEY:
        raise HTTPException(401, "Unauthorized")
    if not client or not client.is_connected():
        raise HTTPException(503, "Offline")
    sent = await client.send_message(BOT_USERNAME, "https://arolinks.com/test")
    msgs = []
    async for msg in client.iter_messages(BOT_USERNAME, limit=5):
        msgs.append({
            "id":       msg.id,
            "from_id":  str(msg.from_id),
            "reply_to": msg.reply_to_msg_id,
            "text":     (msg.text or "")[:200],
        })
    return {"sent_id": sent.id, "last_messages": msgs}

@app.get("/")
async def root():
    return {"service": "AroLink Catcher v9",
            "bot": BOT_USERNAME,
            "endpoints": ["/health", "/debug", "/test-bot?secret=xxx",
                          "POST /submit", "GET /result/{req_id}"]}
