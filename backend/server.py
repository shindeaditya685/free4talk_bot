"""Free4Talk Persistence Bot - FastAPI backend."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import html
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import (
    APIRouter,
    FastAPI,
    Form,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware

from bot_manager import DATA_DIR, bot_manager
from models import Bot, BotCreate, BotRuntimeInfo, BotStatus, BotUpdate, now_iso
from store import create_bot_store
from telegram_bot import TelegramCallbacks, TelegramControlBot

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("server")

FRONTEND_DIST_DIR = ROOT_DIR.parent / "frontend" / "dist"
FRONTEND_INDEX_FILE = FRONTEND_DIST_DIR / "index.html"
bot_store, store_mode = create_bot_store(DATA_DIR.parent / "bots.json")


def _first_env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return ""


def _parse_int_env_set(raw_value: str) -> set[int]:
    values: set[int] = set()
    for part in raw_value.split(","):
        piece = part.strip()
        if not piece:
            continue
        try:
            values.add(int(piece))
        except ValueError:
            logger.warning("Ignoring invalid integer env value: %s", piece)
    return values


AUTH_USERNAME = _first_env("AUTH_USERNAME", "USER_NAME", "user_name")
AUTH_PASSWORD = _first_env("AUTH_PASSWORD", "PASSWORD", "password")
AUTH_ENABLED = bool(AUTH_USERNAME and AUTH_PASSWORD)
AUTH_COOKIE_NAME = os.environ.get("AUTH_COOKIE_NAME", "free4talk_auth")
AUTH_SECRET = os.environ.get("AUTH_SECRET") or hashlib.sha256(
    f"{AUTH_USERNAME}:{AUTH_PASSWORD}:free4talk-auth".encode("utf-8")
).hexdigest()
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
TELEGRAM_BOT_TOKEN = _first_env(
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_TOKEN",
    "BOT_TOKEN",
)
TELEGRAM_ALLOWED_CHAT_IDS = _parse_int_env_set(
    os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "")
)


def _signed_auth_value(value: str) -> str:
    signature = hmac.new(
        AUTH_SECRET.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{value}.{signature}"


def _is_authenticated(cookie_value: str | None) -> bool:
    if not AUTH_ENABLED:
        return True

    if not cookie_value:
        return False

    try:
        value, signature = cookie_value.rsplit(".", 1)
    except ValueError:
        return False

    expected = hmac.new(
        AUTH_SECRET.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return value == "authenticated" and hmac.compare_digest(signature, expected)


def _set_auth_cookie(response: RedirectResponse, secure: bool) -> None:
    response.set_cookie(
        AUTH_COOKIE_NAME,
        _signed_auth_value("authenticated"),
        httponly=True,
        max_age=60 * 60 * 24 * 30,
        path="/",
        samesite="lax",
        secure=secure,
    )


def _clear_auth_cookie(response: RedirectResponse) -> None:
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")


def _is_public_path(path: str) -> bool:
    return path in {"/healthz", "/login", "/logout"}


def _next_target(path: str, query: str = "") -> str:
    if query:
        return f"{path}?{query}"
    return path


def _render_login_page(next_path: str, error: str = "") -> HTMLResponse:
    error_html = ""
    if error:
        error_html = (
            '<p style="margin:16px 0 0;color:#fca5a5;font-size:14px;">'
            f"{html.escape(error)}"
            "</p>"
        )

    safe_next = html.escape(next_path, quote=True)
    return HTMLResponse(
        f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sign in</title>
  <style>
    :root {{ color-scheme: dark; }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background:
        radial-gradient(circle at top, rgb(16 185 129 / 18%), transparent 40%),
        linear-gradient(180deg, #09090b 0%, #111113 100%);
      color: #f4f4f5;
      font-family: Inter, system-ui, sans-serif;
      padding: 24px;
    }}
    .panel {{
      width: min(420px, 100%);
      padding: 28px;
      border-radius: 24px;
      border: 1px solid #27272a;
      background: rgb(17 17 19 / 92%);
      box-shadow: 0 24px 80px rgb(0 0 0 / 35%);
    }}
    .eyebrow {{
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.24em;
      color: #34d399;
      font-family: ui-monospace, monospace;
    }}
    h1 {{ margin: 12px 0 0; font-size: 28px; }}
    p {{ margin: 12px 0 0; color: #a1a1aa; line-height: 1.6; }}
    label {{
      display: block;
      margin: 18px 0 6px;
      font-size: 13px;
      color: #d4d4d8;
    }}
    input {{
      width: 100%;
      border: 1px solid #3f3f46;
      background: #09090b;
      color: #fafafa;
      border-radius: 14px;
      padding: 12px 14px;
      font-size: 14px;
    }}
    button {{
      width: 100%;
      margin-top: 20px;
      border: 0;
      border-radius: 999px;
      background: #34d399;
      color: #052e1a;
      font-weight: 700;
      padding: 12px 16px;
      font-size: 14px;
      cursor: pointer;
    }}
  </style>
</head>
<body>
  <main class="panel">
    <div class="eyebrow">Protected dashboard</div>
    <h1>Sign in</h1>
    <p>Use the credentials configured in <code>backend/.env</code>.</p>
    {error_html}
    <form method="post" action="/login">
      <input type="hidden" name="next" value="{safe_next}">
      <label for="username">Username</label>
      <input id="username" name="username" autocomplete="username" required>
      <label for="password">Password</label>
      <input id="password" name="password" type="password" autocomplete="current-password" required>
      <button type="submit">Open dashboard</button>
    </form>
  </main>
</body>
</html>"""
    )


async def _load_bot(bot_id: str) -> Bot:
    doc = await bot_store.find_bot(bot_id)
    if not doc:
        raise HTTPException(404, "Bot not found")
    return Bot(**doc)


async def _save_bot(bot: Bot) -> None:
    bot.updated_at = now_iso()
    await bot_store.save_bot(bot.model_dump())


def _apply_runtime(bot: Bot) -> Bot:
    runtime = bot_manager.runtime_info(bot.id)

    if runtime["running"]:
        try:
            bot.status = BotStatus(runtime["status"])
        except ValueError:
            pass
        bot.last_message = runtime["last_message"]
        bot.logged_in = runtime["logged_in"]
    elif bot.status not in (BotStatus.STOPPED, BotStatus.IDLE, BotStatus.ERROR):
        bot.status = BotStatus.STOPPED
        bot.last_message = "Not running - click Start"

    return bot


async def _list_bots_with_runtime() -> list[Bot]:
    docs = await bot_store.list_bots()
    return [_apply_runtime(Bot(**doc)) for doc in docs]


async def _create_bot_record(
    nickname: str,
    room_url: str,
    auto_start: bool = True,
) -> Bot:
    bot = Bot(
        nickname=nickname,
        room_url=room_url.strip(),
        auto_start=auto_start,
    )
    await _save_bot(bot)
    return bot


async def _start_bot_record(bot_id: str) -> Bot:
    bot = await _load_bot(bot_id)
    instance = await bot_manager.start_bot(bot.id, bot.nickname, bot.room_url)
    bot.status = BotStatus.STARTING
    bot.display_num = instance.display_num
    bot.vnc_port = instance.vnc_port
    bot.last_message = instance.last_message
    await _save_bot(bot)
    return _apply_runtime(bot)


async def _stop_bot_record(bot_id: str) -> Bot:
    bot = await _load_bot(bot_id)
    await bot_manager.stop_bot(bot_id)
    bot.status = BotStatus.STOPPED
    bot.last_message = "Stopped by user"
    await _save_bot(bot)
    return bot


async def _delete_bot_record(bot_id: str) -> str:
    await _load_bot(bot_id)
    await bot_manager.delete_bot_data(bot_id)
    await bot_store.delete_bot(bot_id)
    return bot_id


telegram_control_bot = TelegramControlBot(
    token=TELEGRAM_BOT_TOKEN,
    callbacks=TelegramCallbacks(
        list_bots=lambda: _telegram_list_bots(),
        create_bot=lambda nickname, room_url, auto_start: _telegram_create_bot(
            nickname, room_url, auto_start
        ),
        start_bot=lambda bot_id: _telegram_start_bot(bot_id),
        stop_bot=lambda bot_id: _telegram_stop_bot(bot_id),
        delete_bot=lambda bot_id: _delete_bot_record(bot_id),
    ),
    allowed_chat_ids=TELEGRAM_ALLOWED_CHAT_IDS,
    public_base_url=PUBLIC_BASE_URL,
)


async def _telegram_list_bots() -> list[dict]:
    return [bot.model_dump() for bot in await _list_bots_with_runtime()]


async def _telegram_create_bot(
    nickname: str,
    room_url: str,
    auto_start: bool,
) -> dict:
    return (await _create_bot_record(nickname, room_url, auto_start)).model_dump()


async def _telegram_start_bot(bot_id: str) -> dict:
    return (await _start_bot_record(bot_id)).model_dump()


async def _telegram_stop_bot(bot_id: str) -> dict:
    return (await _stop_bot_record(bot_id)).model_dump()


async def _handle_bot_event(event: dict) -> None:
    if not telegram_control_bot.enabled:
        return

    if event.get("kind") != "crash":
        return

    await telegram_control_bot.notify_crash(
        bot_id=str(event.get("bot_id", "")),
        nickname=str(event.get("nickname", "Bot")),
        room_url=str(event.get("room_url", "")),
        message=str(event.get("message", "Chromium crashed")),
        recovery_failed=bool(event.get("recovery_failed")),
    )


bot_manager.set_event_notifier(_handle_bot_event)


async def _startup() -> None:
    logger.info("Server starting - bot store %s", store_mode)
    if AUTH_ENABLED:
        logger.info("Dashboard auth enabled for user %s", AUTH_USERNAME)
    else:
        logger.info("Dashboard auth disabled")
    if TELEGRAM_BOT_TOKEN:
        logger.info(
            "Telegram token detected (length=%s, allowed_chats=%s)",
            len(TELEGRAM_BOT_TOKEN),
            len(TELEGRAM_ALLOWED_CHAT_IDS),
        )
    else:
        logger.info(
            "Telegram token missing. Checked env names: TELEGRAM_BOT_TOKEN, TELEGRAM_TOKEN, BOT_TOKEN"
        )
    logger.info("Server starting - auto-start bots with auto_start=True")
    try:
        docs = await bot_store.list_bots()
        for doc in docs:
            if not doc.get("auto_start"):
                continue
            try:
                bot = Bot(**doc)
                logger.info("Auto-starting %s", bot.id)
                await bot_manager.start_bot(bot.id, bot.nickname, bot.room_url)
            except Exception as exc:
                logger.exception("auto-start failed for %s: %s", doc.get("id"), exc)
    except Exception as exc:
        logger.exception("auto-start scan failed: %s", exc)

    if telegram_control_bot.enabled:
        await telegram_control_bot.start()
    else:
        logger.info("Telegram bot disabled")


async def _shutdown() -> None:
    await telegram_control_bot.stop()
    logger.info("Server shutting down - stopping bots")
    for bot_id in list(bot_manager.instances.keys()):
        try:
            await bot_manager.stop_bot(bot_id)
        except Exception:
            pass
    await bot_store.close()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await _startup()
    try:
        yield
    finally:
        await _shutdown()


app = FastAPI(title="Free4Talk Persistence Bot", lifespan=lifespan)
api_router = APIRouter(prefix="/api")


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if not AUTH_ENABLED or _is_public_path(request.url.path):
        return await call_next(request)

    accepts_html = "text/html" in request.headers.get("accept", "")
    if not accepts_html:
        return await call_next(request)

    if _is_authenticated(request.cookies.get(AUTH_COOKIE_NAME)):
        return await call_next(request)

    next_path = _next_target(request.url.path, request.url.query)
    return RedirectResponse(
        url=f"/login?next={quote(next_path, safe='/?=&%:_-')}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.get("/login", include_in_schema=False)
async def login_page(request: Request, next: str = "/"):
    if not AUTH_ENABLED:
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)

    if _is_authenticated(request.cookies.get(AUTH_COOKIE_NAME)):
        return RedirectResponse(next or "/", status_code=status.HTTP_303_SEE_OTHER)

    return _render_login_page(next if next.startswith("/") else "/")


@app.post("/login", include_in_schema=False)
async def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next: str = Form("/"),
):
    if not AUTH_ENABLED:
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)

    safe_next = next if next.startswith("/") else "/"
    if username != AUTH_USERNAME or password != AUTH_PASSWORD:
        return _render_login_page(safe_next, "Invalid username or password")

    response = RedirectResponse(
        url=safe_next,
        status_code=status.HTTP_303_SEE_OTHER,
    )
    _set_auth_cookie(response, secure=request.url.scheme == "https")
    return response


@app.get("/logout", include_in_schema=False)
async def logout():
    response = RedirectResponse(
        url="/login",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    _clear_auth_cookie(response)
    return response


@api_router.get("/")
async def root():
    return {"service": "free4talk-bot", "status": "ok", "store": store_mode}


@api_router.get("/bots", response_model=list[Bot])
async def list_bots():
    return await _list_bots_with_runtime()


@api_router.post("/bots", response_model=Bot)
async def create_bot(payload: BotCreate):
    return await _create_bot_record(
        payload.nickname,
        payload.room_url,
        payload.auto_start,
    )


@api_router.get("/bots/{bot_id}", response_model=Bot)
async def get_bot(bot_id: str):
    return _apply_runtime(await _load_bot(bot_id))


@api_router.patch("/bots/{bot_id}", response_model=Bot)
async def update_bot(bot_id: str, payload: BotUpdate):
    bot = await _load_bot(bot_id)
    data = payload.model_dump(exclude_none=True)

    for key, value in data.items():
        setattr(bot, key, value)

    await _save_bot(bot)
    return bot


@api_router.delete("/bots/{bot_id}")
async def delete_bot(bot_id: str):
    deleted_id = await _delete_bot_record(bot_id)
    return {"deleted": deleted_id}


@api_router.post("/bots/{bot_id}/start", response_model=Bot)
async def start_bot(bot_id: str):
    try:
        return await _start_bot_record(bot_id)
    except Exception as exc:
        logger.exception("start_bot failed")
        bot = await _load_bot(bot_id)
        bot.status = BotStatus.ERROR
        bot.last_message = str(exc)[:250]
        await _save_bot(bot)
        raise HTTPException(500, str(exc))


@api_router.post("/bots/{bot_id}/stop", response_model=Bot)
async def stop_bot(bot_id: str):
    return await _stop_bot_record(bot_id)


@api_router.get("/bots/{bot_id}/status", response_model=BotRuntimeInfo)
async def bot_status(bot_id: str):
    await _load_bot(bot_id)
    return bot_manager.runtime_info(bot_id)


@app.websocket("/api/bots/{bot_id}/vnc-ws")
async def vnc_ws_proxy(websocket: WebSocket, bot_id: str):
    if AUTH_ENABLED and not _is_authenticated(websocket.cookies.get(AUTH_COOKIE_NAME)):
        await websocket.close(code=1008, reason="authentication required")
        return

    instance = bot_manager.get(bot_id)
    if not instance or not instance.running:
        await websocket.close(code=1008, reason="bot not running")
        return

    subprotocols = websocket.headers.get("sec-websocket-protocol", "")
    selected = None
    for subprotocol in ["binary", "base64"]:
        if subprotocol in subprotocols:
            selected = subprotocol
            break

    await websocket.accept(subprotocol=selected)

    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", instance.vnc_port)
    except Exception as exc:
        await websocket.close(code=1011, reason=f"cannot connect to VNC: {exc}")
        return

    async def ws_to_tcp():
        try:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break

                data = message.get("bytes")
                if data is None and message.get("text") is not None:
                    data = base64.b64decode(message["text"])
                if data is None:
                    continue

                writer.write(data)
                await writer.drain()
        except (WebSocketDisconnect, ConnectionError):
            pass
        except Exception as exc:
            logger.debug("ws_to_tcp: %s", exc)
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def tcp_to_ws():
        try:
            while True:
                data = await reader.read(8192)
                if not data:
                    break

                if selected == "base64":
                    await websocket.send_text(base64.b64encode(data).decode("ascii"))
                else:
                    await websocket.send_bytes(data)
        except Exception as exc:
            logger.debug("tcp_to_ws: %s", exc)

    try:
        await asyncio.gather(ws_to_tcp(), tcp_to_ws())
    finally:
        try:
            writer.close()
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass


NOVNC_DIR = Path("/usr/share/novnc")
if NOVNC_DIR.exists():
    app.mount("/api/novnc", StaticFiles(directory=str(NOVNC_DIR)), name="novnc")


VNC_VIEWER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Bot Viewer</title>
<style>
  :root {
    color-scheme: dark;
  }
  html, body {
    margin: 0;
    padding: 0;
    background: #0a0a0b;
    color: #eaeaea;
    font-family: ui-monospace, monospace;
    min-height: 100%;
    overflow: hidden;
  }
  #topbar {
    position: fixed;
    top: 12px;
    left: 12px;
    right: 12px;
    display: flex;
    gap: 10px;
    align-items: center;
    padding: 10px 12px;
    background: rgb(20 20 22 / 78%);
    border: 1px solid rgb(63 63 70 / 78%);
    border-radius: 999px;
    backdrop-filter: blur(14px);
    box-shadow: 0 16px 48px rgb(0 0 0 / 34%);
    z-index: 10;
    font-size: 12px;
    pointer-events: none;
  }
  #label {
    font-weight: 700;
    white-space: nowrap;
  }
  #status {
    padding: 2px 8px;
    border-radius: 999px;
    background: #27272a;
    color: #a1a1aa;
    white-space: nowrap;
  }
  #status.ok { background: #052e1a; color: #4ade80; }
  #status.err { background: #3a0a0a; color: #ef4444; }
  #hint {
    margin-left: auto;
    color: #a1a1aa;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  #screen {
    position: fixed;
    inset: 0;
    background: #000;
  }
  a { color: #4ade80; }
  @media (max-width: 840px) {
    #topbar {
      right: auto;
      max-width: calc(100vw - 24px);
    }
    #hint {
      display: none;
    }
  }
</style>
</head>
<body>
<div id="topbar">
  <strong id="label">VNC - __BOT_ID__</strong>
  <span id="status">connecting...</span>
  <span id="hint">
    Sign in to Google here once. Session persists.
  </span>
</div>
<div id="screen"></div>
<script type="module">
  import RFB from "/api/novnc/core/rfb.js";
  const statusEl = document.getElementById("status");
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const url = `${proto}://${location.host}/api/bots/__BOT_ID__/vnc-ws`;
  const rfb = new RFB(document.getElementById("screen"), url, {});
  rfb.viewOnly = false;
  rfb.clipViewport = false;
  rfb.scaleViewport = true;
  rfb.resizeSession = false;
  rfb.addEventListener("connect", () => {
    statusEl.textContent = "connected";
    statusEl.className = "ok";
  });
  rfb.addEventListener("disconnect", () => {
    statusEl.textContent = "disconnected";
    statusEl.className = "err";
  });
  rfb.addEventListener("credentialsrequired", () => {
    rfb.sendCredentials({ password: "" });
  });
</script>
</body>
</html>"""

VIEWER_FALLBACK_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Bot Viewer</title>
<style>
  html, body {
    margin: 0;
    padding: 0;
    min-height: 100%;
    background: #0b0b0d;
    color: #e5e7eb;
    font-family: ui-sans-serif, system-ui, sans-serif;
  }
  main {
    min-height: 100vh;
    display: grid;
    place-items: center;
    padding: 24px;
  }
  .panel {
    width: min(640px, 100%);
    border: 1px solid #27272a;
    border-radius: 20px;
    background: #111113;
    padding: 28px;
    box-shadow: 0 20px 80px rgb(0 0 0 / 35%);
  }
  .eyebrow {
    color: #34d399;
    font-size: 12px;
    font-family: ui-monospace, monospace;
    text-transform: uppercase;
    letter-spacing: 0.24em;
  }
  h1 {
    margin: 12px 0 0;
    font-size: 28px;
  }
  p {
    margin: 14px 0 0;
    color: #a1a1aa;
    line-height: 1.6;
  }
  code {
    color: #f4f4f5;
    font-family: ui-monospace, monospace;
  }
</style>
</head>
<body>
<main>
  <section class="panel">
    <div class="eyebrow">Viewer unavailable</div>
    <h1>Bot __BOT_ID__</h1>
    <p>__MESSAGE__</p>
  </section>
</main>
</body>
</html>"""


@app.get("/api/bots/{bot_id}/viewer", response_class=HTMLResponse)
async def vnc_viewer(bot_id: str):
    instance = bot_manager.get(bot_id)
    if not instance or not instance.running:
        return HTMLResponse(
            VIEWER_FALLBACK_HTML.replace("__BOT_ID__", bot_id).replace(
                "__MESSAGE__",
                "This bot is not running yet. Start it first, then reload this page.",
            )
        )

    if not instance.vnc_available or not NOVNC_DIR.exists():
        message = instance.last_message or (
            "This bot is running in a local Chromium window on this machine. "
            "Complete Google sign-in there instead of using noVNC."
        )
        return HTMLResponse(
            VIEWER_FALLBACK_HTML.replace("__BOT_ID__", bot_id).replace(
                "__MESSAGE__", message
            )
        )

    return HTMLResponse(VNC_VIEWER_HTML.replace("__BOT_ID__", bot_id))


app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz", include_in_schema=False)
async def healthcheck():
    return {
        "status": "ok",
        "store": store_mode,
        "frontend_built": FRONTEND_INDEX_FILE.exists(),
    }


def _frontend_asset(path: str) -> Path | None:
    if not FRONTEND_DIST_DIR.exists():
        return None

    requested = path.strip("/") or "index.html"
    candidate = (FRONTEND_DIST_DIR / requested).resolve()

    try:
        candidate.relative_to(FRONTEND_DIST_DIR.resolve())
    except ValueError:
        return None

    if candidate.is_file():
        return candidate

    return None


def _frontend_response(path: str):
    asset = _frontend_asset(path)
    if asset is not None:
        return FileResponse(asset)

    if FRONTEND_INDEX_FILE.exists():
        return FileResponse(FRONTEND_INDEX_FILE)

    return HTMLResponse(
        "Frontend build not found. Run `npm run build` in `frontend/` before starting the server.",
        status_code=503,
    )


@app.get("/", include_in_schema=False)
async def frontend_index():
    return _frontend_response("")


@app.get("/{full_path:path}", include_in_schema=False)
async def frontend_routes(full_path: str):
    if full_path == "api" or full_path.startswith("api/"):
        raise HTTPException(404, "Not found")
    return _frontend_response(full_path)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
    )
