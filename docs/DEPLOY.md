# Deploying Everest Auditor — Railway (backend) + Cloudflare Pages (frontend)

A stepwise, do-this-then-that guide. Architecture:

```
Browser ──> Cloudflare Pages (SPA + /api proxy Function) ──> Railway API ──> Postgres(pgvector)
                                                                  │            └─ Worker (same DB)
                                                                  ├─ Cloudflare R2 (4 buckets)
                                                                  ├─ AssemblyAI (STT, webhook back to API)
                                                                  └─ Gemini (google-genai)
```

You will create **3 Railway services** (Postgres, API, Worker), **1 Cloudflare Pages** project, **4 R2 buckets**. No Cloudflare Workers product is needed (the proxy is a Pages Function, included in this repo at `frontend/functions/api/[[path]].js`).

---

## 0. Prerequisites (~10 min)

- Accounts: **Railway**, **Cloudflare** (Pages + R2 — R2 needs a card on file, free tier is generous), **AssemblyAI**, **Google AI Studio** (Gemini API key).
- Push this repo to **GitHub** (Railway + Pages both deploy from GitHub).
- Have these secrets ready: `GEMINI_API_KEY`, `ASSEMBLYAI_API_KEY`, and a strong `JWT_SECRET` (generate: `python -c "import secrets;print(secrets.token_urlsafe(48))"`).

---

## 1. Cloudflare R2 — object storage (~10 min)

1. Cloudflare dashboard → **R2** → create **4 buckets** (names must match `.env`):
   `everest-recordings`, `everest-transcripts`, `everest-kb`, `everest-reports`.
2. R2 → **Manage R2 API Tokens** → **Create API token** → *Object Read & Write*, scoped to those buckets. Save:
   - **Access Key ID** → `R2_ACCESS_KEY_ID`
   - **Secret Access Key** → `R2_SECRET_ACCESS_KEY`
   - **Endpoint** (`https://<accountid>.r2.cloudflarestorage.com`) → `R2_ENDPOINT_URL`
3. **CORS** (needed because the browser fetches presigned report/recording URLs from R2 directly):
   each bucket → Settings → CORS policy →
   ```json
   [{ "AllowedOrigins": ["https://<your-pages-domain>"],
      "AllowedMethods": ["GET","PUT"],
      "AllowedHeaders": ["*"], "ExposeHeaders": ["ETag"], "MaxAgeSeconds": 3600 }]
   ```
   (You'll know the Pages domain after step 6 — come back and fill it in. Use `*` temporarily if you want to test sooner.)

---

## 2. Railway — Postgres with pgvector (~10 min)

The schema runs `CREATE EXTENSION vector` (migration 0001), so the database **must** have pgvector.

**Recommended (guaranteed):** Railway → **New → Database → Add PostgreSQL**, then check pgvector is present:
- Open the DB service → **Data**/**Query** tab → run `CREATE EXTENSION IF NOT EXISTS vector;`
- If it errors with "could not open extension control file", that image lacks pgvector — instead deploy the official image: **New → Empty Service → Deploy from Docker Image →** `pgvector/pgvector:pg16`, add a **Volume** mounted at `/var/lib/postgresql/data`, and set `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` service variables.

Either way, note the DB's connection details (Railway exposes `PGUSER`, `PGPASSWORD`, `PGDATABASE`, and a private host `<db>.railway.internal`).

---

## 3. Railway — API service (~15 min)

1. **New → Deploy from GitHub repo** → pick this repo.
2. Service **Settings**:
   - **Build → Dockerfile Path** = `Dockerfile.api`
   - **Networking → Generate Domain** (e.g. `everest-api.up.railway.app`) — this is your **API public URL**.
   - Keep **replicas = 1** (the start command runs `alembic upgrade head` before serving; a single replica avoids migration races).
3. Service **Variables** (copy from `.env.example`; fill the real values):
   ```
   ENV=production
   LOG_LEVEL=INFO
   DATABASE_URL=postgresql+asyncpg://${{Postgres.PGUSER}}:${{Postgres.PGPASSWORD}}@${{Postgres.RAILWAY_PRIVATE_DOMAIN}}:5432/${{Postgres.PGDATABASE}}
   JWT_SECRET=<your strong secret>
   GEMINI_API_KEY=<...>
   GEMINI_MODEL_PRIMARY=gemini-3.1-pro-preview
   GEMINI_EMBEDDING_MODEL=gemini-embedding-001
   ASSEMBLYAI_API_KEY=<...>
   ASSEMBLYAI_WEBHOOK_SECRET=<random string>
   PUBLIC_BASE_URL=https://everest-api.up.railway.app   # set to THIS service's generated domain
   R2_ENDPOINT_URL=<...>
   R2_ACCESS_KEY_ID=<...>
   R2_SECRET_ACCESS_KEY=<...>
   R2_BUCKET_RECORDINGS=everest-recordings
   R2_BUCKET_TRANSCRIPTS=everest-transcripts
   R2_BUCKET_KB=everest-kb
   R2_BUCKET_REPORTS=everest-reports
   ```
   > **Two gotchas baked in above:** the URL scheme must be `postgresql+asyncpg://` (the app uses asyncpg, Railway's auto `DATABASE_URL` is plain `postgresql://`), and it uses the **private** `RAILWAY_PRIVATE_DOMAIN` so API↔DB traffic stays on Railway's internal network (faster, no egress). `${{Postgres.VAR}}` is Railway's reference-variable syntax — rename `Postgres` to match your DB service name.
4. Deploy. Watch **Deploy Logs** for `Running upgrade ... -> 0012` then uvicorn startup.
5. Verify: open `https://everest-api.up.railway.app/readyz` → should return `{"status":"ok"}` (200).

---

## 4. Railway — Worker service (~5 min)

1. In the same project: **New → GitHub repo →** same repo again (a second service).
2. **Settings → Dockerfile Path** = `Dockerfile.worker`. No domain needed (no inbound traffic).
3. **Variables**: copy the same API variables, including `PUBLIC_BASE_URL` and `ASSEMBLYAI_WEBHOOK_SECRET`; omit only `PORT`. The worker submits AssemblyAI transcription jobs, so it must know the API's public URL to attach `https://<api-domain>/webhooks/assemblyai` as the callback. If `PUBLIC_BASE_URL` or `ASSEMBLYAI_WEBHOOK_SECRET` is missing, the app still works via reconciler polling, but calls can sit in `AWAITING_TRANSCRIPT` until the next poll.
4. Deploy. Logs should show `worker.stt_webhook_enabled` when the webhook is configured, or `worker.stt_webhook_disabled` when it is falling back to polling. The worker does **not** run migrations (by design).

---

## 5. Seed the super admin (~3 min, one-off)

After the API's first deploy (migrations applied), create the first login:

- Railway → API service → **⋯ → Run a command** (or `railway run` locally against the prod env):
  ```
  PYTHONPATH=backend python backend/scripts/seed_creds.py
  ```
- This creates `admin@everest.local` / `EverestAdmin#2026` (+ the Everest supervisor/agent). **Change the admin password** afterwards via the app's Users panel, or edit the script's constants before running.

---

## 6. Cloudflare Pages — frontend (~10 min)

1. Cloudflare → **Workers & Pages → Create → Pages → Connect to Git** → this repo.
2. Build settings:
   - **Root directory** = `frontend`
   - **Build command** = `npm run build`
   - **Output directory** = `dist`
   - Framework preset: *Vite* (or None).
3. **Settings → Environment variables → Production**:
   ```
   API_BASE_URL = https://everest-api.up.railway.app
   ```
   (read by `frontend/functions/api/[[path]].js`, which proxies `/api/*` → the Railway API).
4. Deploy. You get a domain like `everest-auditor.pages.dev` — this is your **app URL**.
5. Go back and finish **R2 CORS** (step 1.3) and **API CORS is not needed** (same-origin via the proxy).

> The `functions/` directory is already in the repo; Cloudflare Pages auto-detects it during the build — no Wrangler config required.

---

## 7. Wire the STT webhook (already done if the worker has PUBLIC_BASE_URL)

AssemblyAI calls the API back directly (not through Pages). `PUBLIC_BASE_URL` on the **worker** must equal the **Railway API** public domain, and `ASSEMBLYAI_WEBHOOK_SECRET` must match the API service. Nothing else to configure - the worker builds the callback URL from it. If either value is missing, STT falls back to reconciler polling; this is functional but slower.

---

## 8. Smoke test in production (~5 min)

1. Open the Pages URL → log in as `admin@everest.local`.
2. Create a portfolio → folder → upload a short recording (FULL option).
3. Watch it move PENDING_TRANSCRIPTION → PENDING_JUDGE → DONE (the Lifecycle view helps).
4. Open the report; download the HTML. If you configured a custom template/schema in Agent Studio, confirm it renders.
5. Check Railway **worker logs** show the job processing, and **API logs** show the AssemblyAI webhook hit.

---

## 9. Gotchas & ops checklist

- **pgvector** — the #1 failure mode. If migrations fail at 0001 with an extension error, your Postgres image lacks pgvector (see step 2 fallback).
- **`postgresql+asyncpg://`** — not plain `postgresql://`. The app won't connect otherwise.
- **API replicas = 1** while the start command owns migrations. To scale the API later, move `alembic upgrade head` to a Railway **release command** and drop it from the Dockerfile CMD, then scale freely.
- **Worker scaling** — safe to run multiple worker replicas; the queue uses `FOR UPDATE SKIP LOCKED`, so they won't double-process.
- **Upload size** — recordings are proxied browser → Pages Function → API. Typical call audio (a few–tens of MB) is fine; very large files can hit Worker/Pages body limits — if you ingest big files, point uploads straight at the API instead.
- **Secrets** — only ever set via Railway/Pages variables. Nothing secret is committed; `.env` is gitignored.
- **JWT_SECRET** — must be a strong random value in prod (the default is `change-me-in-prod`).
- **Costs / rate limits** — the `# commented` knobs in `.env.example` (GEMINI_RPM, DAILY_CAP_PER_PORTFOLIO, etc.) override the in-code defaults; see `docs/RATE_LIMITS_AND_COST.md`.
- **Custom domain** — add it on the Pages project; update R2 CORS `AllowedOrigins` and `PUBLIC_BASE_URL` is unaffected (that's the API domain, not the app domain).
