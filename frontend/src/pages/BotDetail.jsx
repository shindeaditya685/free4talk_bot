import {
  startTransition,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { api, viewerUrl, startBot, stopBot } from "../lib/api";
import { Button } from "../components/ui/button";
import { Badge } from "../components/ui/badge";
import { toast } from "sonner";
import {
  ArrowLeft,
  ExternalLink,
  Maximize2,
  Play,
  RefreshCw,
  Square,
} from "lucide-react";
import { Link } from "../components/AppLink";
import { navigate } from "../lib/router";

const COLORS = {
  idle: "bg-zinc-800 text-zinc-300",
  starting: "bg-amber-900/60 text-amber-200",
  waiting_login: "bg-sky-900/60 text-sky-200",
  joining: "bg-indigo-900/60 text-indigo-200",
  in_room: "bg-emerald-900/70 text-emerald-200",
  disconnected: "bg-orange-900/60 text-orange-200",
  error: "bg-red-900/70 text-red-200",
  stopped: "bg-zinc-800 text-zinc-400",
};

export default function BotDetail({ botId }) {
  const [bot, setBot] = useState(null);
  const [iframeKey, setIframeKey] = useState(0);
  const mountedRef = useRef(true);
  const viewerShellRef = useRef(null);

  const refreshBot = useCallback(async () => {
    try {
      const nextBot = (await api.get(`/bots/${botId}`)).data;

      if (!mountedRef.current) {
        return;
      }

      startTransition(() => {
        setBot(nextBot);
      });
    } catch (error) {
      if (!mountedRef.current) {
        return;
      }

      if (error?.status === 404) {
        toast.error("This bot no longer exists");
        navigate("/", { replace: true });
        return;
      }

      toast.error("Failed to load");
    }
  }, [botId]);

  useEffect(() => {
    mountedRef.current = true;

    const pollBot = () => {
      void refreshBot();
    };
    const initialLoad = window.setTimeout(pollBot, 0);
    const poller = window.setInterval(pollBot, 3000);

    return () => {
      window.clearTimeout(initialLoad);
      window.clearInterval(poller);
      mountedRef.current = false;
    };
  }, [refreshBot]);

  const handleStart = async () => {
    try {
      await startBot(botId);
      toast.success("Starting...");
      void refreshBot();
      window.setTimeout(() => setIframeKey((key) => key + 1), 2500);
    } catch (error) {
      toast.error(error?.response?.data?.detail || "Start failed");
    }
  };

  const handleStop = async () => {
    try {
      await stopBot(botId);
      toast.success("Stopped");
      void refreshBot();
    } catch {
      toast.error("Stop failed");
    }
  };

  const handleViewerFullscreen = async () => {
    const shell = viewerShellRef.current;

    if (!shell) {
      return;
    }

    try {
      if (document.fullscreenElement === shell) {
        await document.exitFullscreen();
        return;
      }

      await shell.requestFullscreen();
    } catch {
      toast.error("Could not open fullscreen viewer");
    }
  };

  if (!bot) {
    return <div className="p-10 text-zinc-500">Loading...</div>;
  }

  const isRunning = bot.status !== "stopped" && bot.status !== "idle";
  const currentViewerUrl = viewerUrl(bot.id);

  return (
    <div className="flex min-h-screen flex-col" data-testid="bot-detail-root">
      <header className="flex items-center gap-3 border-b border-zinc-800 bg-[#0f0f11] px-4 py-3">
        <Link to="/" className="text-zinc-400 hover:text-zinc-100">
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <div className="min-w-0 flex-1">
          <div className="truncate font-semibold text-zinc-50">
            {bot.nickname}
          </div>
          <div className="truncate font-mono text-[11px] text-zinc-500">
            {bot.room_url}
          </div>
        </div>
        <Badge
          className={`rounded-full px-3 py-1 font-mono text-[11px] uppercase ${COLORS[bot.status] || COLORS.idle}`}
          data-testid={`detail-status-${bot.status}`}
        >
          {bot.status.replace("_", " ")}
        </Badge>
        {!isRunning ? (
          <Button
            onClick={handleStart}
            size="sm"
            data-testid="detail-start"
            className="rounded-full bg-emerald-400 text-black hover:bg-emerald-300"
          >
            <Play className="mr-1 h-3.5 w-3.5" /> Start
          </Button>
        ) : (
          <Button
            onClick={handleStop}
            size="sm"
            variant="secondary"
            data-testid="detail-stop"
            className="rounded-full bg-zinc-800 text-zinc-200 hover:bg-zinc-700"
          >
            <Square className="mr-1 h-3.5 w-3.5" /> Stop
          </Button>
        )}
        <Button
          onClick={() => setIframeKey((key) => key + 1)}
          size="sm"
          variant="outline"
          data-testid="detail-reload"
          className="rounded-full border-zinc-700 bg-transparent text-zinc-200 hover:bg-zinc-800"
        >
          <RefreshCw className="h-3.5 w-3.5" />
        </Button>
        {isRunning ? (
          <>
            <Button
              onClick={handleViewerFullscreen}
              size="sm"
              variant="outline"
              className="rounded-full border-zinc-700 bg-transparent text-zinc-200 hover:bg-zinc-800"
            >
              <Maximize2 className="h-3.5 w-3.5" />
            </Button>
            <Button
              asChild
              size="sm"
              variant="outline"
              className="rounded-full border-zinc-700 bg-transparent text-zinc-200 hover:bg-zinc-800"
            >
              <a href={currentViewerUrl} target="_blank" rel="noreferrer">
                <ExternalLink className="h-3.5 w-3.5" />
              </a>
            </Button>
          </>
        ) : null}
        <a
          href="/logout"
          className="text-sm text-zinc-500 transition-colors hover:text-zinc-100"
        >
          Log out
        </a>
      </header>

      <div
        ref={viewerShellRef}
        className="relative flex-1 overflow-hidden bg-black"
      >
        {isRunning ? (
          <>
            <iframe
              key={iframeKey}
              data-testid="vnc-iframe"
              title="vnc"
              src={currentViewerUrl}
              className="absolute inset-0 h-full w-full border-0"
              allow="clipboard-write; clipboard-read"
            />
            {bot.last_message ? (
              <div className="pointer-events-none absolute right-4 bottom-4 left-4 z-10 flex justify-start">
                <div className="max-w-full rounded-full border border-zinc-700/70 bg-black/70 px-3 py-1.5 text-[11px] text-zinc-300 backdrop-blur">
                  {bot.last_message}
                </div>
              </div>
            ) : null}
          </>
        ) : (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-4 text-zinc-500">
            <div>Bot is stopped.</div>
            <Button
              onClick={handleStart}
              data-testid="detail-start-big"
              className="rounded-full bg-emerald-400 text-black hover:bg-emerald-300"
            >
              <Play className="mr-2 h-4 w-4" /> Start bot to open viewer
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
