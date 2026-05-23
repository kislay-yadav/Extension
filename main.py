"""
AroLink Catcher Backend v2
==========================
- Session is created via https://tg-n7dh.onrender.com/register (web login)
  so the IP stays consistent on Render — no ban risk.
- Extension sends arolinks URL → userbot sends to channel → channel bot
  resolves it → userbot captures reply → returns direct URL to extension.

Environment variables to set on Render:
  TG_API_ID       → from my.telegram.org
  TG_API_HASH     → from my.telegram.org
  TG_SESSION      → from /get-session after web login
  TG_CHANNEL      → @yourchannel or -100xxxxxxxxxx
  SECRET_KEY      → any password you choose
"""

import os, asyncio, re, uuid, logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from telethon import TelegramClient, events
from telethon.sessions import StringSession

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("arocatcher")

# ── Config ────────────────────────────────────────────────────────
API_ID      = int(os.environ.get("TG_API_ID", "21952127"))
API_HASH    = os.environ.get("TG_API_HASH", "e0a3741bb3b132947d86d8fc6218eebe")
SESSION_STR = os.environ.get("TG_SESSION", "1BVtsOIUBu47QAKSS-BHZ8E2-w_DzQf-lCUwdX7F-HuTSV82Lb68-eha9jYHLC-19Vt1fg7BDdilHEUDwS0mWNkNp45nBScSfl8rUUN9O5hBPG7dug-dTDsGIpfBsJUXlXKsPDK4-G5njJJzLnJf0oYv61iHo-zIrKq48IjeY-n8-7PgRLQ0ChQJGZD4tg8XFat9zSTTOaIXqpdDNnCaRVj3zv4cQX9xEjRctTS9Ir1CGTHgaLrK4hFCJjvL5gznWFhaUBN6xmgmnWkJxGVN7bT3UvkdrmXee6IlMRZE4LBvDkEFpeXVlYmpNrpebG13zO4jyYN7RuPUPAYWziV8Dlak2DDWFtyg=")
CHANNEL     = os.environ.get("TG_CHANNEL", "")
SECRET_KEY  = os.environ.get("SECRET_KEY", "changeme")

# ── State ─────────────────────────────────────────────────────────
pending      : dict[str, asyncio.Future] = {}
last_code    : dict[str, str]            = {}   # req_id → arolink code
client       : TelegramClient | None     = None

# ── Channel message handler ───────────────────────────────────────
def setup_handlers(c: TelegramClient):
    @c.on(events.NewMessage(chats=CHANNEL))
    async def on_msg(event):
        text = event.message.text or ""
        log.info(f"Channel message: {text[:80]}")

        # Extract URLs from message
        urls = re.findall(r'https?://\S+', text)
        # Filter out ad/intermediate domains
        SKIP = ['arolinks.com','mahnokari.com','t.co','bit.ly']
        dest_urls = [u.rstrip('.,)') for u in urls if not any(s in u for s in SKIP)]

        if not dest_urls:
            return

        # Match to pending request by code or req_id tag
        matched = False
        for req_id, code in list(last_code.items()):
            if code in text or f"#req_{req_id}" in text:
                fut = pending.get(req_id)
                if fut and not fut.done():
                    fut.set_result(dest_urls[0])
                    matched = True
                    log.info(f"✅ Matched req {req_id} → {dest_urls[0]}")
                break

        # Fallback: resolve oldest pending request
        if not matched and pending:
            oldest = next(iter(pending))
            fut = pending[oldest]
            if not fut.done():
                fut.set_result(dest_urls[0])
                log.info(f"✅ Fallback match req {oldest} → {dest_urls[0]}")

# ── Lifespan ──────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global client
    if SESSION_STR and API_ID and API_HASH:
        client = TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH)
        setup_handlers(client)
        await client.start()
        me = await client.get_me()
        log.info(f"✅ Userbot online: {me.first_name} (@{me.username})")
    else:
        log.warning("⚠️  TG_SESSION / API_ID / API_HASH not set — userbot offline")
    yield
    if client:
        await client.disconnect()

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Models ────────────────────────────────────────────────────────
class ResolveReq(BaseModel):
    url: str
    secret: str

# ── /resolve — main endpoint called by extension ─────────────────
@app.post("/resolve")
async def resolve(req: ResolveReq):
    if req.secret != SECRET_KEY:
        raise HTTPException(401, "Invalid secret key")

    if "arolinks.com" not in req.url:
        raise HTTPException(400, "Not an arolinks URL")

    if not client or not client.is_connected():
        raise HTTPException(503, "Userbot not connected. Set TG_SESSION in Render env vars.")

    if not CHANNEL:
        raise HTTPException(503, "TG_CHANNEL not configured.")

    code   = req.url.rstrip('/').split('/')[-1].split('?')[0]
    req_id = uuid.uuid4().hex[:8]

    loop = asyncio.get_event_loop()
    fut  = loop.create_future()
    pending[req_id]   = fut
    last_code[req_id] = code

    # Send to channel
    msg = f"🔗 {req.url}\n\n#req_{req_id}"
    await client.send_message(CHANNEL, msg)
    log.info(f"📤 Sent to channel: {req.url} [req={req_id}]")

    try:
        result = await asyncio.wait_for(fut, timeout=30.0)
        return {"success": True, "url": result, "code": code}
    except asyncio.TimeoutError:
        return {"success": False, "error": "Channel didn't reply in 30s. Check your channel bot.", "code": code}
    finally:
        pending.pop(req_id, None)
        last_code.pop(req_id, None)

# ── /health ───────────────────────────────────────────────────────
@app.get("/health")
async def health():
    if client and client.is_connected():
        me = await client.get_me()
        return {"status": "ok", "userbot": me.first_name, "username": me.username}
    return {"status": "offline", "userbot": None}

# ── /session-setup — web UI to guide session creation ────────────
@app.get("/session-setup", response_class=HTMLResponse)
async def session_setup():
    return HTMLResponse(f"""
<!DOCTYPE html>
<html>
<head>
  <title>AroLink — Session Setup</title>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{background:#0d0d0d;color:#f0f0f0;font-family:monospace;
         display:flex;align-items:center;justify-content:center;
         min-height:100vh;padding:20px}}
    .card{{background:#161616;border:1px solid #252525;border-radius:10px;
           padding:28px;max-width:480px;width:100%}}
    h2{{font-size:18px;margin-bottom:6px;color:#00ff88}}
    p{{color:#666;font-size:12px;margin-bottom:20px;line-height:1.6}}
    .step{{background:#0a0a0a;border:1px solid #252525;border-radius:6px;
           padding:12px 14px;margin-bottom:10px;font-size:12px;line-height:1.8}}
    .step b{{color:#00ccff}}
    a{{color:#00ff88;text-decoration:none}}
    a:hover{{text-decoration:underline}}
    .btn{{
      display:block;width:100%;margin-top:16px;padding:12px;
      background:#00ff88;color:#000;font-weight:bold;font-size:13px;
      border:none;border-radius:6px;cursor:pointer;text-align:center;
      text-decoration:none;font-family:monospace;
    }}
    code{{background:#252525;padding:2px 6px;border-radius:3px;font-size:11px}}
  </style>
</head>
<body>
<div class="card">
  <h2>🔐 Session Setup</h2>
  <p>Create your Telegram session using the web login below.<br>
     Both login and bot run on Render — same IP, no ban risk.</p>

  <div class="step">
    <b>Step 1</b> — Open the session creator:<br>
    <a href="https://tg-n7dh.onrender.com/register" target="_blank">
      https://tg-n7dh.onrender.com/register
    </a>
  </div>
  <div class="step">
    <b>Step 2</b> — Enter your phone number + OTP → copy the session string
  </div>
  <div class="step">
    <b>Step 3</b> — Go to your Render dashboard → Environment → add:<br>
    <code>TG_SESSION</code> = (paste session string)<br>
    <code>TG_API_ID</code> = your api_id from my.telegram.org<br>
    <code>TG_API_HASH</code> = your api_hash<br>
    <code>TG_CHANNEL</code> = @yourchannel<br>
    <code>SECRET_KEY</code> = any password
  </div>
  <div class="step">
    <b>Step 4</b> — Redeploy → check <a href="/health">/health</a> → userbot online ✓
  </div>

  <a class="btn" href="https://tg-n7dh.onrender.com/register" target="_blank">
    → Open Session Creator
  </a>
</div>
</body>
</html>
""")

# ── / root ────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {
        "service": "AroLink Catcher v2",
        "session_setup": "/session-setup",
        "health": "/health",
        "resolve": "POST /resolve"
    }
