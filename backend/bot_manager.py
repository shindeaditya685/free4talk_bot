"""
Free4Talk Bot Manager.

Runs a persistent, headful Chromium (via Playwright) inside a virtual X display
(Xvfb) with x11vnc attached, so the user can connect via noVNC (served by
FastAPI) to perform the one-time Google login. After login, the bot keeps the
tab alive, clicks the "Click on anywhere to start" overlay, and rejoins if
kicked/disconnected.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
import socket
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Dict, Optional

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright, BrowserContext, Page

logger = logging.getLogger("bot_manager")

WINDOWS = os.name == "nt"
DEFAULT_DATA_ROOT = Path(__file__).parent / "data" if WINDOWS else Path("/data")
raw_data_dir = os.environ.get("BOT_DATA_DIR")

if WINDOWS and raw_data_dir in (None, "", "/app/data"):
    data_root = DEFAULT_DATA_ROOT
else:
    data_root = Path(raw_data_dir or DEFAULT_DATA_ROOT)

DATA_DIR = data_root / "bots"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DISPLAY_BASE = 99
VNC_PORT_BASE = 5900
SCREEN_WIDTH = 1366
SCREEN_HEIGHT = 768
SCREEN_GEOMETRY = f"{SCREEN_WIDTH}x{SCREEN_HEIGHT}x24"
NAVIGATION_TIMEOUT_MS = 20000
CRASH_PAGE_MARKERS = (
    "aw, snap!",
    "something went wrong while displaying this webpage",
    "error code:",
)
CRASH_ALERT_COOLDOWN_SECONDS = 300

BotEventNotifier = Callable[[dict], Awaitable[None]]


def _supports_managed_vnc() -> bool:
    return (
        not WINDOWS
        and shutil.which("Xvfb") is not None
        and shutil.which("x11vnc") is not None
    )


def _find_free_port(start: int, end: int, used: set[int]) -> int:
    for p in range(start, end):
        if p in used:
            continue
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", p))
            return p
        except OSError:
            continue
    raise RuntimeError("no free port")


def _find_free_display(used: set[int]) -> int:
    for n in range(DISPLAY_BASE, DISPLAY_BASE + 200):
        if n in used:
            continue
        lock = Path(f"/tmp/.X{n}-lock")
        if not lock.exists():
            return n
    raise RuntimeError("no free X display")


def _cleanup_profile_locks(user_data_dir: Path) -> None:
    """Remove stale Chromium singleton lock artifacts from persistent profiles."""
    lock_names = (
        "SingletonCookie",
        "SingletonLock",
        "SingletonSocket",
    )

    for name in lock_names:
        target = user_data_dir / name
        try:
            if target.is_symlink() or target.is_file():
                target.unlink()
            elif target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
        except FileNotFoundError:
            pass
        except Exception as exc:
            logger.warning("failed to clear stale profile lock %s: %s", target, exc)


@dataclass
class BotInstance:
    bot_id: str
    nickname: str
    room_url: str
    display_num: int
    vnc_port: int
    user_data_dir: Path
    xvfb_proc: Optional[subprocess.Popen] = None
    vnc_proc: Optional[subprocess.Popen] = None
    playwright_ctx: Optional[object] = None
    browser_context: Optional[BrowserContext] = None
    page: Optional[Page] = None
    monitor_task: Optional[asyncio.Task] = None
    running: bool = False
    stop_requested: bool = False
    status: str = "idle"
    last_message: str = ""
    in_room: bool = False
    logged_in: bool = False
    vnc_available: bool = False
    fullscreen_applied: bool = False
    event_notifier: BotEventNotifier | None = None
    last_crash_alert_at: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def set_status(self, status: str, message: str = "") -> None:
        next_message = message or self.last_message
        status_changed = status != self.status
        message_changed = next_message != self.last_message

        self.status = status
        if message:
            self.last_message = message

        if status_changed or message_changed:
            logger.info(
                f"[{self.bot_id[:8]}] status={self.status} msg={self.last_message}"
            )

    def login_instructions(self) -> str:
        if self.vnc_available:
            return "Open the VNC viewer and sign in with Google"
        return "A local Chromium window was opened. Sign in there once with Google"

    def login_progress_message(self) -> str:
        if self.vnc_available:
            return "Google sign-in in progress (use VNC)"
        return "Google sign-in in progress (local browser window)"

    def login_required_message(self) -> str:
        if self.vnc_available:
            return "Sign in with Google via VNC"
        return "Sign in with Google in the local browser window"

    def _is_browser_crash_error(self, exc: Exception) -> bool:
        message = str(exc).lower()
        return any(
            marker in message
            for marker in (
                "target crashed",
                "page crashed",
                "target page, context or browser has been closed",
                "browser has been closed",
            )
        )

    async def _notify_event(self, payload: dict) -> None:
        if not self.event_notifier:
            return

        try:
            await self.event_notifier(payload)
        except Exception as exc:
            logger.warning("bot event notify failed for %s: %s", self.bot_id, exc)

    async def _notify_crash(self, message: str, recovery_failed: bool = False) -> None:
        now = asyncio.get_running_loop().time()
        if not recovery_failed and now - self.last_crash_alert_at < CRASH_ALERT_COOLDOWN_SECONDS:
            return

        if not recovery_failed:
            self.last_crash_alert_at = now

        await self._notify_event(
            {
                "kind": "crash",
                "bot_id": self.bot_id,
                "nickname": self.nickname,
                "room_url": self.room_url,
                "message": message,
                "recovery_failed": recovery_failed,
            }
        )

    async def _goto_room(self, page: Page) -> None:
        await page.goto(
            self.room_url,
            wait_until="domcontentloaded",
            timeout=NAVIGATION_TIMEOUT_MS,
        )

    async def _ensure_active_page(self) -> Page:
        if not self.browser_context:
            raise RuntimeError("Browser context is unavailable")

        if self.page and not self.page.is_closed():
            return self.page

        self.page = await self.browser_context.new_page()
        self.fullscreen_applied = False
        return self.page

    async def _recover_room_page(self, reason: str) -> bool:
        if not self.browser_context:
            self.set_status("error", f"{reason}. Browser context is unavailable")
            await self._notify_crash(
                f"{reason}. Browser context is unavailable",
                recovery_failed=True,
            )
            return False

        self.set_status("joining", reason)
        self.in_room = False
        self.fullscreen_applied = False

        if self.page and not self.page.is_closed():
            try:
                await self.page.close()
            except Exception:
                pass

        self.page = None

        try:
            page = await self._ensure_active_page()
            await self._goto_room(page)
        except Exception as exc:
            logger.warning("page recovery failed for %s: %s", self.bot_id, exc)
            self.set_status("error", f"{reason}. Recovery failed: {str(exc)[:120]}")
            await self._notify_crash(
                f"{reason}. Recovery failed: {str(exc)[:120]}",
                recovery_failed=True,
            )
            return False

        return True

    async def _is_crash_page(self, url: str) -> bool:
        if url.startswith("chrome-error://"):
            return True

        if not self.page or self.page.is_closed():
            return False

        try:
            page_text = await self.page.evaluate(
                """() => `${document.title || ''}\n${document.body?.innerText || ''}`"""
            )
        except Exception as exc:
            return self._is_browser_crash_error(exc)

        normalized = str(page_text).lower()
        return all(marker in normalized for marker in CRASH_PAGE_MARKERS[:2])

    async def _launch_browser_context(
        self, launch_args: list[str], env: dict[str, str]
    ) -> BrowserContext:
        launch_options = {
            "user_data_dir": str(self.user_data_dir),
            "headless": False,
            "viewport": {"width": SCREEN_WIDTH, "height": SCREEN_HEIGHT},
            "args": launch_args,
            "env": env,
            "ignore_default_args": ["--enable-automation"],
            "user_agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        }

        channels = [None]
        if WINDOWS:
            channels.extend(["msedge", "chrome"])

        last_error: Exception | None = None
        for channel in channels:
            try:
                options = dict(launch_options)
                if channel:
                    options["channel"] = channel
                return await self.playwright_ctx.chromium.launch_persistent_context(
                    **options
                )
            except PlaywrightError as exc:
                last_error = exc
                message = str(exc)
                if not WINDOWS or "Executable doesn't exist" not in message:
                    raise
                logger.warning("playwright launch failed for channel %s: %s", channel, exc)

        if last_error is not None:
            raise last_error
        raise RuntimeError("Failed to launch browser context")

    async def start(self) -> None:
        self.stop_requested = False
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        _cleanup_profile_locks(self.user_data_dir)
        env = os.environ.copy()

        if _supports_managed_vnc():
            self.vnc_available = True
            self.set_status("starting", "Launching virtual display")

            self.xvfb_proc = subprocess.Popen(
                [
                    "Xvfb",
                    f":{self.display_num}",
                    "-screen",
                    "0",
                    SCREEN_GEOMETRY,
                    "-nolisten",
                    "tcp",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            await asyncio.sleep(1.2)

            self.vnc_proc = subprocess.Popen(
                [
                    "x11vnc",
                    "-display",
                    f":{self.display_num}",
                    "-rfbport",
                    str(self.vnc_port),
                    "-nopw",
                    "-localhost",
                    "-forever",
                    "-shared",
                    "-quiet",
                    "-noxdamage",
                    "-noxrecord",
                    "-noxfixes",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            await asyncio.sleep(0.8)
            env["DISPLAY"] = f":{self.display_num}"
        else:
            self.vnc_available = False
            self.set_status("starting", "Launching local browser window")

        self.set_status("starting", "Launching browser")
        self.playwright_ctx = await async_playwright().start()
        launch_args = [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-blink-features=AutomationControlled",
            "--use-fake-ui-for-media-stream",
            "--mute-audio",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--window-position=0,0",
            f"--window-size={SCREEN_WIDTH},{SCREEN_HEIGHT}",
            "--start-maximized",
        ]

        self.browser_context = await self._launch_browser_context(launch_args, env)

        try:
            await self.browser_context.grant_permissions(
                ["microphone"], origin="https://www.free4talk.com"
            )
        except Exception as e:
            logger.warning(f"grant_permissions failed: {e}")

        if self.browser_context.pages:
            self.page = self.browser_context.pages[0]
        else:
            self.page = await self.browser_context.new_page()

        await self._goto_room(self.page)
        self.running = True
        self.set_status("waiting_login", self.login_instructions())

        self.monitor_task = asyncio.create_task(self._monitor_loop())

    async def _monitor_loop(self) -> None:
        while self.running and not self.stop_requested:
            try:
                await asyncio.sleep(5)
                try:
                    page = await self._ensure_active_page()
                except Exception as exc:
                    self.set_status("error", f"Browser page missing: {str(exc)[:120]}")
                    await asyncio.sleep(5)
                    continue

                url = page.url
                if await self._is_crash_page(url):
                    await self._notify_crash("Chromium crashed - reopening room")
                    recovered = await self._recover_room_page(
                        "Chromium crashed - reopening room"
                    )
                    if not recovered:
                        await asyncio.sleep(5)
                    continue

                on_google = "accounts.google.com" in url
                on_f4t = "free4talk.com" in url

                clicked_start = False
                try:
                    clicked_start = await page.evaluate("""() => {
                            const nodes = document.querySelectorAll('body *');
                            for (const n of nodes) {
                                const t = (n.textContent || '').trim().toLowerCase();
                                if (t === 'click on anywhere to start' ||
                                    t === 'click anywhere to start') {
                                    const rect = n.getBoundingClientRect();
                                    if (rect.width > 0) {
                                        const ev = new MouseEvent('click', {
                                            bubbles:true,
                                            cancelable:true,
                                            view:window,
                                        });
                                        (n.closest('div') || n).dispatchEvent(ev);
                                        document.body.click();
                                        return true;
                                    }
                                }
                            }
                            return false;
                        }""")
                except Exception:
                    clicked_start = False

                if on_f4t:
                    try:
                        has_login_btn = await page.evaluate("""() => {
                                const txt = (document.body.innerText || '').toLowerCase();
                                return txt.includes('sign in') || txt.includes('login with google')
                                    || txt.includes('login google');
                            }""")
                        self.logged_in = not has_login_btn
                    except Exception:
                        pass

                currently_in_room = on_f4t and "/room/" in url
                self.in_room = currently_in_room and self.logged_in

                if on_google:
                    self.set_status("waiting_login", self.login_progress_message())
                elif not self.logged_in and on_f4t:
                    self.set_status("waiting_login", self.login_required_message())
                elif self.logged_in and not currently_in_room:
                    self.set_status("joining", "Rejoining room")
                    try:
                        await self._goto_room(page)
                    except Exception as e:
                        logger.warning(f"goto failed: {e}")
                elif currently_in_room and self.logged_in:
                    if self.vnc_available and not self.fullscreen_applied:
                        try:
                            await page.keyboard.press("F11")
                            self.fullscreen_applied = True
                            self.set_status("in_room", "Entered fullscreen room view")
                        except Exception as e:
                            logger.warning(f"fullscreen failed: {e}")

                    msg = "In room (silent presence)"
                    if clicked_start:
                        msg = "Clicked start overlay"
                    self.set_status("in_room", msg)
                else:
                    self.set_status("starting", f"At {url[:80]}")

                try:
                    await page.mouse.move(
                        640 + (int(asyncio.get_event_loop().time()) % 5), 400
                    )
                except Exception:
                    pass
            except Exception as e:
                if self._is_browser_crash_error(e):
                    await self._notify_crash("Browser tab crashed - reopening room")
                    recovered = await self._recover_room_page(
                        "Browser tab crashed - reopening room"
                    )
                    if recovered:
                        continue
                logger.exception(f"monitor loop error: {e}")
                self.set_status("error", str(e)[:200])
                await asyncio.sleep(5)

        logger.info(f"[{self.bot_id[:8]}] monitor loop exited")

    async def stop(self) -> None:
        self.stop_requested = True
        self.running = False
        self.set_status("stopped", "Stopping")
        if self.monitor_task:
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            finally:
                self.monitor_task = None
        try:
            if self.browser_context:
                await self.browser_context.close()
        except Exception as e:
            logger.warning(f"ctx close err: {e}")
        finally:
            self.browser_context = None
            self.page = None
        try:
            if self.playwright_ctx:
                await self.playwright_ctx.stop()
        except Exception as e:
            logger.warning(f"pw stop err: {e}")
        finally:
            self.playwright_ctx = None
        for proc in (self.vnc_proc, self.xvfb_proc):
            if proc and proc.poll() is None:
                try:
                    proc.send_signal(signal.SIGTERM)
                    await asyncio.sleep(0.3)
                    if proc.poll() is None:
                        proc.kill()
                except Exception:
                    pass
        self.vnc_proc = None
        self.xvfb_proc = None
        self.set_status("stopped", "Stopped")


class BotManager:
    def __init__(self) -> None:
        self.instances: Dict[str, BotInstance] = {}
        self._start_tasks: Dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self._event_notifier: BotEventNotifier | None = None

    def set_event_notifier(self, notifier: BotEventNotifier | None) -> None:
        self._event_notifier = notifier

    def _used_displays(self) -> set[int]:
        return {b.display_num for b in self.instances.values()}

    def _used_ports(self) -> set[int]:
        return {b.vnc_port for b in self.instances.values()}

    def _is_active_instance(self, inst: BotInstance) -> bool:
        return inst.running or inst.status in {
            "starting",
            "waiting_login",
            "joining",
            "in_room",
            "disconnected",
        }

    async def _run_start(self, inst: BotInstance) -> BotInstance:
        try:
            await inst.start()
            return inst
        except BaseException:
            try:
                await inst.stop()
            except Exception:
                logger.exception("cleanup failed after start_bot error for %s", inst.bot_id)

            async with self._lock:
                if self.instances.get(inst.bot_id) is inst:
                    self.instances.pop(inst.bot_id, None)
            raise
        finally:
            async with self._lock:
                current_task = self._start_tasks.get(inst.bot_id)
                if current_task is asyncio.current_task():
                    self._start_tasks.pop(inst.bot_id, None)

    async def start_bot(self, bot_id: str, nickname: str, room_url: str) -> BotInstance:
        async with self._lock:
            existing = self.instances.get(bot_id)
            start_task = self._start_tasks.get(bot_id)

            if start_task is None and existing and self._is_active_instance(existing):
                return existing

            if start_task is None:
                display_num = _find_free_display(self._used_displays())
                vnc_port = _find_free_port(
                    VNC_PORT_BASE, VNC_PORT_BASE + 200, self._used_ports()
                )
                user_data_dir = DATA_DIR / bot_id
                inst = BotInstance(
                    bot_id=bot_id,
                    nickname=nickname,
                    room_url=room_url,
                    display_num=display_num,
                    vnc_port=vnc_port,
                    user_data_dir=user_data_dir,
                    event_notifier=self._event_notifier,
                )
                self.instances[bot_id] = inst
                start_task = asyncio.create_task(self._run_start(inst))
                self._start_tasks[bot_id] = start_task

        return await start_task

    async def stop_bot(self, bot_id: str) -> None:
        async with self._lock:
            inst = self.instances.get(bot_id)
            start_task = self._start_tasks.get(bot_id)

        if start_task and not start_task.done():
            start_task.cancel()
            try:
                await start_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

            async with self._lock:
                inst = self.instances.get(bot_id)

        if not inst:
            return
        await inst.stop()
        async with self._lock:
            if self.instances.get(bot_id) is inst:
                self.instances.pop(bot_id, None)

    async def delete_bot_data(self, bot_id: str) -> None:
        await self.stop_bot(bot_id)
        udd = DATA_DIR / bot_id
        if udd.exists():
            shutil.rmtree(udd, ignore_errors=True)

    def get(self, bot_id: str) -> Optional[BotInstance]:
        return self.instances.get(bot_id)

    def runtime_info(self, bot_id: str) -> dict:
        inst = self.instances.get(bot_id)
        if not inst:
            return {
                "id": bot_id,
                "status": "stopped",
                "last_message": "",
                "in_room": False,
                "running": False,
                "logged_in": False,
                "vnc_available": False,
            }
        return {
            "id": bot_id,
            "status": inst.status,
            "last_message": inst.last_message,
            "in_room": inst.in_room,
            "running": inst.running,
            "logged_in": inst.logged_in,
            "vnc_available": inst.running and inst.vnc_available,
        }


bot_manager = BotManager()
