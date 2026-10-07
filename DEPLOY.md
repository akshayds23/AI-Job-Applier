# Deploying AutoApplier on Vercel + Neon

One Vercel project serves everything on one domain:

```
https://your-app.vercel.app/        -> Next.js frontend   (service "web", folder frontend/)
https://your-app.vercel.app/api/... -> FastAPI backend     (service "api", folder backend/)
                                         └─ Neon Postgres (DATABASE_URL)
```

`vercel.json` at the project root defines both services, the `/api` routing and a daily cron.

## How the backend runs on Vercel

Vercel runs the backend as on-demand functions (max 300 s per request on Hobby, 800 s on Pro),
with no always-on process and no memory shared between requests. The app is built for that:

- **Long work runs as background jobs** stored in Neon (`background_jobs`): job searches,
  resume/cover-letter preparation and inbox sync each run in steps of at most ~4 minutes.
  Progress is saved after every job analysed, so nothing is lost between steps.
- **Rate limits pause a job instead of a server waiting**: the job is rescheduled for the moment
  your key resets. After your max wait (Settings -> AI keys) it continues with the non-AI fallback.
- **Who runs the steps**: the browser keeps work moving while you have the app open, and the cron
  call (`/api/internal/cron`) picks up anything left plus scheduled searches and inbox checks.
- **LaTeX resumes**: the build step (`backend/scripts/vercel_build.sh`) bundles the Tectonic engine
  and its packages, so PDFs compile in a few seconds.
- **Not available on Vercel (or any server)**: "Auto-fill application", which opens a browser window
  on the computer running the backend. Users get "Open application page" + the ATS-safe PDF.
  It still works when you run AutoApplier on your own PC.

### Hobby (free) vs Pro

| | Hobby | Pro |
|---|---|---|
| Searches, preparing documents, reply tracking | Yes | Yes |
| Built-in cron (scheduled searches, hourly inbox check) | **Once a day** | Every minute/hour |
| Max time per step | 300 s | up to 800 s |

On Hobby you can still get hourly background work for free with an external pinger - see step 4.

---

## 1. Put the code on GitHub

`frontend/` contains its own `.git` folder from the original template; remove it first:

```powershell
Remove-Item -Recurse -Force frontend\.git
git init
git add .
git commit -m "AutoApplier"
git remote add origin https://github.com/<you>/autoapplier.git   # create an empty private repo first
git branch -M main
git push -u origin main
```

## 2. Neon database - where the connection URL goes

1. https://console.neon.tech -> **New project** (region near your users, e.g. AWS Singapore for India).
2. **Connect** -> choose **Pooled connection** -> copy the string. It looks like
   `postgresql://neondb_owner:...@ep-xxxx-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require`
3. Put it in **one environment variable called `DATABASE_URL`**:
   - **On Vercel**: Project -> Settings -> Environment Variables -> `DATABASE_URL` = the string
     (or install Neon from the Vercel Marketplace, which adds `DATABASE_URL` for you).
   - **On your PC** (optional, to use Neon locally too): the `DATABASE_URL=` line in `.env` at the project root.
   The backend converts it automatically (SSL, pooler settings) - paste it exactly as Neon shows it.
4. Optional - copy your local data (accounts, profile, AI keys, jobs, applications) into Neon:
   ```powershell
   cd backend
   ..\.venv312\Scripts\python.exe -m scripts.migrate_sqlite_to_postgres "<the Neon string>"
   ```
   Tables are created automatically on first start, so this is only needed to keep existing data.

## 3. Vercel project

1. https://vercel.com/new -> import the GitHub repo. Leave **Root Directory** as the repository root
   (`vercel.json` there defines the services). Framework preset: "Services"/Other.
2. **Environment Variables** (Production + Preview):

   | Name | Value |
   |---|---|
   | `DATABASE_URL` | Neon pooled connection string (step 2) |
   | `NEXTAUTH_SECRET` | a long random string - generate with `python -c "import secrets; print(secrets.token_urlsafe(48))"`. **Use the same value as your local `.env` if you migrated data** - stored API keys are encrypted with it. Never change it later. |
   | `CRON_SECRET` | another long random string (Vercel sends it to the cron endpoint) |
   | `ENVIRONMENT` | `production` |

   Not needed: `NEXT_PUBLIC_API_URL` (the frontend calls `/api` on the same domain) and CORS settings.
3. **Deploy**. Open `https://<project>.vercel.app/api/healthz` - it should return `{"ok": true}`.
4. Register, then add an AI key in **Settings -> AI keys** (every user brings their own).

## 4. (Hobby only, optional) hourly background work for free

Vercel Hobby runs the built-in cron once a day. For hourly inbox checks and scheduled searches, add a
free external pinger, e.g. https://cron-job.org:
- URL: `https://<project>.vercel.app/api/internal/cron`
- Schedule: every 30-60 minutes
- Header: `Authorization: Bearer <your CRON_SECRET>`

On Pro, change `"schedule"` in `vercel.json` to `"0 * * * *"` instead.

## Alternative: always-on container (Render / Railway / Fly.io)

`backend/Dockerfile` + `render.yaml` run the same backend as one always-on container (it advances
jobs itself every few seconds). Use it if you prefer a server to serverless; deploy the frontend
on Vercel with `NEXT_PUBLIC_API_URL=https://<backend>/api` in that case.
