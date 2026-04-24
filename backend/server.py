"""Free4Talk Persistence Bot - FastAPI backend."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware

from bot_manager import DATA_DIR, bot_manager
from models import Bot, BotCreate, BotRuntimeInfo, BotStatus, BotUpdate, now_iso
from store import create_bot_store

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


async def _load_bot(bot_id: str) -> Bot:
    doc = await bot_store.find_bot(bot_id)
    if not doc:
        raise HTTPException(404, "Bot not found")
    return Bot(**doc)


async def _save_bot(bot: Bot) -> None:
    bot.updated_at = now_iso()
    await bot_store.save_bot(bot.model_dump())


async def _startup() -> None:
    logger.info("Server starting - bot store %s", store_mode)
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


async def _shutdown() -> None:
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


@api_router.get("/")
async def root():
    return {"service": "free4talk-bot", "status": "ok", "store": store_mode}


@api_router.get("/bots", response_model=list[Bot])
async def list_bots():
    docs = await bot_store.list_bots()
    bots: list[Bot] = []

    for doc in docs:
        bot = Bot(**doc)
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

        bots.append(bot)

    return bots


@api_router.post("/bots", response_model=Bot)
async def create_bot(payload: BotCreate):
    bot = Bot(
        nickname=payload.nickname,
        room_url=payload.room_url.strip(),
        auto_start=payload.auto_start,
    )
    await _save_bot(bot)
    return bot


@api_router.get("/bots/{bot_id}", response_model=Bot)
async def get_bot(bot_id: str):
    bot = await _load_bot(bot_id)
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
    await _load_bot(bot_id)
    await bot_manager.delete_bot_data(bot_id)
    await bot_store.delete_bot(bot_id)
    return {"deleted": bot_id}


@api_router.post("/bots/{bot_id}/start", response_model=Bot)
async def start_bot(bot_id: str):
    bot = await _load_bot(bot_id)

    try:
        instance = await bot_manager.start_bot(bot.id, bot.nickname, bot.room_url)
        bot.status = BotStatus.STARTING
        bot.display_num = instance.display_num
        bot.vnc_port = instance.vnc_port
        bot.last_message = instance.last_message
        await _save_bot(bot)
    except Exception as exc:
        logger.exception("start_bot failed")
        bot.status = BotStatus.ERROR
        bot.last_message = str(exc)[:250]
        await _save_bot(bot)
        raise HTTPException(500, str(exc))

    return bot


@api_router.post("/bots/{bot_id}/stop", response_model=Bot)
async def stop_bot(bot_id: str):
    bot = await _load_bot(bot_id)
    await bot_manager.stop_bot(bot_id)
    bot.status = BotStatus.STOPPED
    bot.last_message = "Stopped by user"
    await _save_bot(bot)
    return bot


@api_router.get("/bots/{bot_id}/status", response_model=BotRuntimeInfo)
async def bot_status(bot_id: str):
    await _load_bot(bot_id)
    return bot_manager.runtime_info(bot_id)


@app.websocket("/api/bots/{bot_id}/vnc-ws")
async def vnc_ws_proxy(websocket: WebSocket, bot_id: str):
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
  html, body {
    margin: 0;
    padding: 0;
    background: #0a0a0b;
    color: #eaeaea;
    font-family: ui-monospace, monospace;
    height: 100%;
    overflow: hidden;
  }
  #topbar {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    display: flex;
    gap: 12px;
    align-items: center;
    padding: 8px 14px;
    background: #141416;
    border-bottom: 1px solid #27272a;
    z-index: 10;
    font-size: 12px;
  }
  #status {
    padding: 2px 8px;
    border-radius: 999px;
    background: #27272a;
    color: #a1a1aa;
  }
  #status.ok { background: #052e1a; color: #4ade80; }
  #status.err { background: #3a0a0a; color: #ef4444; }
  #screen {
    position: absolute;
    top: 42px;
    left: 0;
    right: 0;
    bottom: 0;
    background: #000;
  }
  a { color: #4ade80; }
</style>
</head>
<body>
<div id="topbar">
  <strong>VNC - __BOT_ID__</strong>
  <span id="status">connecting...</span>
  <span style="margin-left:auto;color:#71717a">
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
