# Free4Talk Presence Bot

This project runs a persistent Playwright browser that stays inside a Free4Talk room after you leave. You sign in with Google once inside the bot's own browser session, and the session data stays on disk so the bot can reconnect after restarts.

## What changed

- FastAPI now serves the built React frontend, so production is a single app and a single URL.
- The backend can run with either:
  - a local JSON metadata store on disk, or
  - MongoDB if `MONGO_URL` is configured.
- Bot browser profiles are stored on a persistent disk path (`/data` in Docker/Fly).
- Fly.io deployment files are included.

## Local development

### Backend

1. Create `backend/.env` from `backend/.env.example`.
2. Install backend dependencies in your preferred environment.
3. Start the API:

```powershell
cd backend
python server.py
```

If `MONGO_URL` is empty, the backend stores bot metadata in `backend/data/bots.json` on Windows or `/data/bots.json` on Linux.

### Telegram control bot

The backend can also run a Telegram control bot that manages your Free4Talk bots over chat. It uses long polling, so you do not need to configure Telegram webhooks.

Set these env vars in `backend/.env` or your deployment platform:

```env
TELEGRAM_BOT_TOKEN=123456:telegram-bot-token
TELEGRAM_ALLOWED_CHAT_IDS=123456789
PUBLIC_BASE_URL=https://your-app-domain.example
```

Notes:

- `TELEGRAM_BOT_TOKEN` enables the Telegram bot.
- `TELEGRAM_ALLOWED_CHAT_IDS` is optional but strongly recommended. It locks control to your own Telegram chat ids.
- `PUBLIC_BASE_URL` is optional. If set, the Telegram bot can send dashboard and viewer links.

Supported Telegram commands:

- `/start` or `/help`
- `/bots`
- `/create Name | https://www.free4talk.com/room/...`
- `/run <id-or-prefix>`
- `/stop <id-or-prefix>`
- `/status <id-or-prefix>`
- `/viewer <id-or-prefix>`
- `/delete <id-or-prefix>`
- `/dashboard`
- `/whoami`

The Telegram bot also sends crash alerts when Chromium shows an `Aw, Snap!` page or the room tab crashes. For proactive alerts, set `TELEGRAM_ALLOWED_CHAT_IDS` so the app knows exactly which chat ids should receive them.

### Frontend

1. Create `frontend/.env` from `frontend/.env.example` if you want to override the backend URL in Vite dev.
2. Start Vite:

```powershell
cd frontend
npm install
npm run dev
```

## Railway

Railway is workable for this project because it supports Docker deployments and persistent volumes. The main catch is pricing and trial restrictions:

- New accounts start with a trial that includes a one-time `$5` grant for up to `30` days.
- After the trial, Railway reverts to the Free plan with `$1` of monthly credit.
- Free and Trial plans have a default volume size of `0.5GB`.
- Limited Trial accounts have outbound network restrictions, which may break Google login and Free4Talk access.

Sources: [Railway free trial](https://docs.railway.com/pricing/free-trial), [Railway pricing](https://docs.railway.com/pricing), [Railway volumes](https://docs.railway.com/volumes/reference), [Railway using volumes](https://docs.railway.com/guides/volumes), [Railway config as code](https://docs.railway.com/reference/config-as-code), [Railway healthchecks](https://docs.railway.com/deployments/healthchecks), [Railway Dockerfiles](https://docs.railway.com/builds/dockerfiles).

### Railway deploy

1. Push this repo to GitHub.
2. In Railway, create a new project and deploy from the GitHub repo.
3. Railway should detect the root [Dockerfile](/C:/Users/91845/Downloads/bot/Dockerfile:1). The repo also includes [railway.toml](/C:/Users/91845/Downloads/bot/railway.toml:1) with:
   - Dockerfile builder
   - `/healthz` healthcheck
   - restart policy
4. Add a volume to the service and mount it at `/data`.
5. Set these service variables:

```env
BOT_DATA_DIR=/data
CORS_ORIGINS=*
HOST=0.0.0.0
PUBLIC_BASE_URL=https://your-service-name.up.railway.app
TELEGRAM_BOT_TOKEN=123456:telegram-bot-token
TELEGRAM_ALLOWED_CHAT_IDS=123456789
```

6. Do not set `PORT` manually unless Railway support asks you to. Railway injects it automatically and the backend already reads it.
7. Leave `MONGO_URL` empty unless you want MongoDB. The app can use the built-in file store on the volume.
8. Deploy and open the generated Railway domain.

### Railway notes

- If Railway puts your account on a Limited Trial, Google login automation will probably fail because outbound access is restricted. In that case, connect GitHub and let Railway verify the account first.
- If the `0.5GB` trial/free volume is too small for Chromium profiles, you may need to upgrade or prune unused bot profiles.
- For this app, mount the volume at `/data` and keep browser/session state there. That is what the container is already configured for.

## Production / Fly.io

As of April 24, 2026, Fly.io is no longer a guaranteed zero-cost host for new accounts; pricing is usage-based and the old free hobby plan is legacy-only. The app is still packaged for Fly.io because it fits the persistent-volume browser-session workflow well. Sources: [Fly.io pricing](https://fly.io/docs/about/pricing/), [Fly.io volumes](https://fly.io/docs/volumes/overview/), [Fly.io app config](https://fly.io/docs/reference/configuration/).

### Deploy

1. Install `flyctl` and log in.
2. Change the `app` name in `fly.toml` if `free4talk-presence-bot` is already taken.
3. Create the app without deploying first:

```bash
fly launch --no-deploy
```

4. Create a persistent volume in your primary region:

```bash
fly volumes create bot_data --size 5 --region sin
```

5. Optional: if you want MongoDB instead of the built-in JSON store, set secrets:

```bash
fly secrets set MONGO_URL="your-mongo-url" DB_NAME="free4talk"
```

6. Deploy:

```bash
fly deploy
```

### After deploy

1. Open the deployed app.
2. Create a bot with your Free4Talk room URL.
3. Start the bot and open the viewer.
4. Sign in with Google once in the noVNC browser session.
5. Leave the room yourself; the bot keeps the session alive.
