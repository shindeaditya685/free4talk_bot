import "@/App.css";
import Dashboard from "./pages/Dashboard";
import BotDetail from "./pages/BotDetail";
import { Toaster } from "./components/ui/sonner";
import { Link } from "./components/AppLink";
import { usePathname } from "./lib/router";

function matchBotDetail(pathname) {
  const match = /^\/bots\/([^/]+)\/?$/.exec(pathname);

  return match ? decodeURIComponent(match[1]) : null;
}

function NotFound() {
  return (
    <div className="flex min-h-screen items-center justify-center px-6">
      <div className="text-center">
        <div className="text-xs font-mono uppercase tracking-[0.3em] text-zinc-500">
          Route not found
        </div>
        <h1 className="mt-3 text-3xl font-bold text-zinc-50">Wrong room.</h1>
        <p className="mt-3 text-sm text-zinc-400">
          This page does not exist anymore, but the dashboard is one click away.
        </p>
        <Link
          to="/"
          className="mt-6 inline-flex rounded-full bg-emerald-400 px-5 py-2 text-sm font-semibold text-black transition-colors hover:bg-emerald-300"
        >
          Back to dashboard
        </Link>
      </div>
    </div>
  );
}

function App() {
  const pathname = usePathname();
  const botId = matchBotDetail(pathname);

  let content = <NotFound />;

  if (pathname === "/" || pathname === "") {
    content = <Dashboard />;
  } else if (botId) {
    content = <BotDetail botId={botId} />;
  }

  return (
    <div className="App min-h-screen bg-[#0b0b0d] text-zinc-100">
      {content}
      <Toaster theme="dark" richColors position="top-right" />
    </div>
  );
}

export default App;
