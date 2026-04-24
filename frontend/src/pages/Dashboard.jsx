import {
  startTransition,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { listBots, createBot, deleteBot, startBot, stopBot } from "../lib/api";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Card } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
  DialogFooter,
} from "../components/ui/dialog";
import { Switch } from "../components/ui/switch";
import { toast } from "sonner";
import { Link } from "../components/AppLink";
import {
  Play,
  Square,
  Trash2,
  Plus,
  ExternalLink,
  Eye,
  Loader2,
  Radio,
} from "lucide-react";

const STATUS_COLORS = {
  idle: "bg-zinc-800 text-zinc-300",
  starting: "bg-amber-900/60 text-amber-200",
  waiting_login: "bg-sky-900/60 text-sky-200",
  joining: "bg-indigo-900/60 text-indigo-200",
  in_room: "bg-emerald-900/70 text-emerald-200",
  disconnected: "bg-orange-900/60 text-orange-200",
  error: "bg-red-900/70 text-red-200",
  stopped: "bg-zinc-800 text-zinc-400",
};

const StatusPill = ({ status, message }) => (
  <div className="flex flex-col gap-1">
    <Badge
      data-testid={`bot-status-${status}`}
      className={`rounded-full px-3 py-1 text-[11px] font-mono tracking-wide uppercase ${
        STATUS_COLORS[status] || STATUS_COLORS.idle
      }`}
    >
      {status === "in_room" && (
        <Radio className="mr-1 inline h-3 w-3 animate-pulse" />
      )}
      {status.replace("_", " ")}
    </Badge>
    {message ? (
      <span className="line-clamp-1 text-[11px] text-zinc-500">{message}</span>
    ) : null}
  </div>
);

export default function Dashboard() {
  const [bots, setBots] = useState([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({
    nickname: "",
    room_url: "",
    auto_start: true,
  });
  const mountedRef = useRef(true);

  const fetchBots = useCallback(async () => {
    try {
      const nextBots = await listBots();

      if (!mountedRef.current) {
        return;
      }

      startTransition(() => {
        setBots(nextBots);
      });
    } catch {
      if (!mountedRef.current) {
        return;
      }

      toast.error("Failed to load bots");
    } finally {
      if (mountedRef.current) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;

    const loadBots = () => {
      void fetchBots();
    };
    const initialLoad = window.setTimeout(loadBots, 0);
    const poller = window.setInterval(loadBots, 4000);

    return () => {
      window.clearTimeout(initialLoad);
      window.clearInterval(poller);
      mountedRef.current = false;
    };
  }, [fetchBots]);

  const handleCreate = async (event) => {
    event.preventDefault();

    if (!form.nickname || !form.room_url) {
      toast.error("Fill nickname and room URL");
      return;
    }

    setCreating(true);

    try {
      const bot = await createBot(form);

      toast.success("Bot created");
      setOpen(false);
      setForm({ nickname: "", room_url: "", auto_start: true });
      await fetchBots();

      try {
        await startBot(bot.id);
        toast.info("Bot starting...");
        void fetchBots();
      } catch (error) {
        toast.error(error?.response?.data?.detail || "Failed to start bot");
      }
    } catch (error) {
      toast.error(error?.response?.data?.detail || "Create failed");
    } finally {
      setCreating(false);
    }
  };

  const handleStart = async (id) => {
    try {
      await startBot(id);
      toast.success("Starting...");
      void fetchBots();
    } catch (error) {
      toast.error(error?.response?.data?.detail || "Start failed");
    }
  };

  const handleStop = async (id) => {
    try {
      await stopBot(id);
      toast.success("Stopped");
      void fetchBots();
    } catch (error) {
      toast.error(error?.response?.data?.detail || "Stop failed");
    }
  };

  const handleDelete = async (id) => {
    if (!window.confirm("Delete this bot and its session data?")) {
      return;
    }

    try {
      await deleteBot(id);
      toast.success("Deleted");
      void fetchBots();
    } catch (error) {
      toast.error(error?.response?.data?.detail || "Delete failed");
    }
  };

  return (
    <div className="mx-auto max-w-6xl px-6 py-10" data-testid="dashboard-root">
      <header className="mb-10 flex items-end justify-between">
        <div>
          <div className="mb-2 font-mono text-[11px] uppercase tracking-[0.3em] text-emerald-400">
            // free4talk presence bot
          </div>
          <h1 className="text-4xl font-bold tracking-tight text-zinc-50 sm:text-5xl lg:text-6xl">
            Ghost in the Room
          </h1>
          <p className="mt-3 max-w-xl text-sm text-zinc-400">
            Persistent Chromium bots that hold your Free4talk study room open -
            sign in once with Google via the built-in VNC, and the bot stays
            there forever.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <a
            href="/logout"
            className="text-sm text-zinc-500 transition-colors hover:text-zinc-200"
          >
            Log out
          </a>
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
              <Button
                data-testid="new-bot-button"
                className="h-11 rounded-full bg-emerald-400 px-6 font-semibold text-black hover:bg-emerald-300"
              >
                <Plus className="mr-2 h-4 w-4" /> New bot
              </Button>
            </DialogTrigger>
            <DialogContent className="border-zinc-800 bg-[#111113] text-zinc-100">
              <DialogHeader>
                <DialogTitle>Deploy a new bot</DialogTitle>
              </DialogHeader>
              <form onSubmit={handleCreate} className="mt-2 space-y-4">
                <div>
                  <Label htmlFor="nick" className="text-xs text-zinc-400">
                    Nickname
                  </Label>
                  <Input
                    id="nick"
                    data-testid="form-nickname"
                    value={form.nickname}
                    onChange={(event) =>
                      setForm({ ...form, nickname: event.target.value })
                    }
                    placeholder="e.g. study-vc666"
                    className="mt-1 border-zinc-800 bg-zinc-900"
                  />
                </div>
                <div>
                  <Label htmlFor="url" className="text-xs text-zinc-400">
                    Free4talk room URL
                  </Label>
                  <Input
                    id="url"
                    data-testid="form-room-url"
                    value={form.room_url}
                    onChange={(event) =>
                      setForm({ ...form, room_url: event.target.value })
                    }
                    placeholder="https://www.free4talk.com/room/vc666?key=694049"
                    className="mt-1 border-zinc-800 bg-zinc-900 font-mono text-xs"
                  />
                </div>
                <div className="flex items-center justify-between">
                  <Label htmlFor="auto" className="text-xs text-zinc-400">
                    Auto-start on server restart
                  </Label>
                  <Switch
                    id="auto"
                    data-testid="form-auto-start"
                    checked={form.auto_start}
                    onCheckedChange={(value) =>
                      setForm({ ...form, auto_start: value })
                    }
                  />
                </div>
                <DialogFooter>
                  <Button
                    type="submit"
                    data-testid="form-submit"
                    disabled={creating}
                    className="rounded-full bg-emerald-400 text-black hover:bg-emerald-300"
                  >
                    {creating && (
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    )}
                    Deploy bot
                  </Button>
                </DialogFooter>
              </form>
            </DialogContent>
          </Dialog>
        </div>
      </header>

      {loading ? (
        <div className="text-sm text-zinc-500">Loading...</div>
      ) : bots.length === 0 ? (
        <Card
          data-testid="empty-state"
          className="border-dashed border-zinc-800 bg-[#111113] p-14 text-center"
        >
          <div className="text-2xl font-semibold text-zinc-200">
            No bots yet
          </div>
          <p className="mx-auto mt-2 max-w-md text-sm text-zinc-500">
            Create your first bot. You&apos;ll be able to sign in with Google
            once via the VNC viewer.
          </p>
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
          {bots.map((bot) => {
            const isRunning = bot.status !== "stopped" && bot.status !== "idle";

            return (
              <Card
                key={bot.id}
                data-testid={`bot-card-${bot.id}`}
                className="border-zinc-800 bg-[#111113] p-5 transition-colors hover:border-zinc-700"
              >
                <div className="mb-4 flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-lg font-semibold text-zinc-50">
                      {bot.nickname}
                    </div>
                    <a
                      href={bot.room_url}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex max-w-full items-center gap-1 truncate font-mono text-xs text-zinc-500 hover:text-zinc-300"
                    >
                      {bot.room_url}
                      <ExternalLink className="h-3 w-3 flex-shrink-0" />
                    </a>
                  </div>
                  <StatusPill status={bot.status} message={bot.last_message} />
                </div>

                <div className="mb-4 grid grid-cols-2 gap-2 font-mono text-[11px] text-zinc-500">
                  <div>
                    logged in:{" "}
                    <span
                      className={
                        bot.logged_in ? "text-emerald-400" : "text-zinc-500"
                      }
                    >
                      {bot.logged_in ? "yes" : "no"}
                    </span>
                  </div>
                  <div>
                    auto-start:{" "}
                    <span
                      className={
                        bot.auto_start ? "text-emerald-400" : "text-zinc-500"
                      }
                    >
                      {bot.auto_start ? "yes" : "no"}
                    </span>
                  </div>
                </div>

                <div className="flex flex-wrap gap-2">
                  {!isRunning ? (
                    <Button
                      data-testid={`start-${bot.id}`}
                      onClick={() => handleStart(bot.id)}
                      size="sm"
                      className="rounded-full bg-emerald-400 text-black hover:bg-emerald-300"
                    >
                      <Play className="mr-1 h-3.5 w-3.5" />
                      Start
                    </Button>
                  ) : (
                    <Button
                      data-testid={`stop-${bot.id}`}
                      onClick={() => handleStop(bot.id)}
                      size="sm"
                      variant="secondary"
                      className="rounded-full bg-zinc-800 text-zinc-200 hover:bg-zinc-700"
                    >
                      <Square className="mr-1 h-3.5 w-3.5" /> Stop
                    </Button>
                  )}

                  <Button
                    asChild
                    data-testid={`view-${bot.id}`}
                    size="sm"
                    variant="outline"
                    className="rounded-full border-zinc-700 bg-transparent text-zinc-200 hover:bg-zinc-800"
                  >
                    <Link to={`/bots/${bot.id}`}>
                      <Eye className="mr-1 h-3.5 w-3.5" /> Open viewer
                    </Link>
                  </Button>

                  <Button
                    data-testid={`delete-${bot.id}`}
                    onClick={() => handleDelete(bot.id)}
                    size="sm"
                    variant="ghost"
                    className="ml-auto rounded-full text-zinc-500 hover:bg-red-950/40 hover:text-red-400"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </Card>
            );
          })}
        </div>
      )}

      <footer className="mt-16 font-mono text-[11px] text-zinc-600">
        tip: after first Google sign-in in the VNC viewer, the session cookie
        persists on disk - stop/start the bot and it stays logged in.
      </footer>
    </div>
  );
}
