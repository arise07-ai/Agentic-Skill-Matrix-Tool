# Deploying to Railway

Two services in one Railway project. Only the **frontend** is public. The **backend** has no public address and is reached over
Railway's private network, so nobody can call the API except through the website.

```
browser ──https──▶ frontend (Next.js)  ──private network──▶ backend (FastAPI + SQLite + Chroma)
                                                               └─ volume mounted at /app/data
```

No separate Chroma or Redis service is needed. The vector index runs inside the backend and uses the same volume as the database.

## Before you start
- The code is in a GitHub repo (Railway deploys from it). `.env` must NOT be committed (it is already in `.gitignore`).
- Add the files from this package to the repo: `backend/Dockerfile`, `backend/.dockerignore`, `frontend/Dockerfile`, `frontend/.dockerignore`.
- Recommended: run `npm install` once in `frontend/` and commit the generated `package-lock.json`, so builds use fixed versions.
- Check your plan's limits on volumes and memory on Railway's pricing page. A Chroma index plus the Python libraries needs some memory, and a
  volume is required (free trials may not include one).

## Steps
1. **New project -> Deploy from GitHub repo**, pick your repo. Railway may create one service; use it for the backend.
2. **Backend service**
   - Settings -> **Service name**: `backend` (the frontend finds it by this name).
   - Settings -> **Root Directory**: `/backend`. Railway builds the Dockerfile it finds there.
   - Settings -> **Networking**: do NOT generate a public domain.
   - **Volumes** -> add a volume and set the **mount path** to `/app/data`.
   - **Variables** -> Raw Editor -> paste `railway.env.backend.example` and fill in the values.
     Make a signing secret with: `python -c "import secrets;print(secrets.token_urlsafe(48))"`
   - Settings -> **Healthcheck path**: `/api/health`.
   - Settings -> make sure there is **one** replica (the app keeps jobs and login lock-out in memory).
3. **Frontend service**: add another service from the same repo.
   - **Service name**: `frontend`. **Root Directory**: `/frontend`.
   - **Variables** -> paste `railway.env.frontend.example`.
   - **Networking** -> **Generate Domain** (or attach your own domain).
4. Deploy the backend first, then the frontend. After the frontend has its domain, the backend variable `CORS_ORIGINS` resolves to it; redeploy the
   backend once if it was empty at first.
5. Open the frontend's address and log in with `ADMIN_USERNAME` / `ADMIN_PASSWORD`. Then **remove `ADMIN_PASSWORD`** from the backend variables.
6. Create the other accounts on the Users page. Import your employee data.

## If something does not work
| Symptom | Likely cause and fix |
|---|---|
| Frontend loads but every API call fails (502 / "Internal Server Error") | The frontend cannot reach the backend. Check the service is named `backend`, `PORT=8000` is set on it, and `BACKEND_URL` on the frontend is `http://backend.railway.internal:8000`; then redeploy the frontend. If it still fails, add `HOST=::` to the backend variables (some Railway networks use IPv6) and redeploy the backend. |
| You log in and are logged out again, or the login does nothing | `AUTH_COOKIE_SECURE=true` needs https (Railway domains are https). Check `AUTH_SECRET` is set; without it every restart signs everyone out. |
| Data disappears after a deploy | The volume is not mounted at `/app/data`, or the app wrote somewhere else. Check Volumes on the backend. |
| Backend keeps restarting | Open the deploy logs. A missing `OPENAI_API_KEY` does not stop it, but a volume or port problem does. The healthcheck waits for `/api/health`; the first start also builds the vector index in the background. |
| A long import times out | The frontend proxy allows 10 minutes, but Railway's public edge has its own request time limit (check its current value in their docs). Import in smaller batches. |

## Keeping it safe
- Railway stores secrets as service variables. Never put keys in the repo or the Dockerfiles.
- Turn on **volume backups** for the backend volume (Volume -> Backups) and try a restore once. Check which backup options your plan includes.
- Set a spending limit on your OpenAI account and watch Railway's usage page.
- Update in this order: test the change locally, deploy, check `/api/health`, then log in and look at Approvals.
- The backend container runs as root so it can write to the volume. If you later switch it to a non-root user, set `RAILWAY_RUN_UID=0` on that service or the volume will not be writable.

## Not done by these files (code changes still open from the security review)
Role limits on the taxonomy / requested-skills / reindex routes, a stronger login lock-out, no raw error text in 500 responses, rate limits on the
AI endpoints, and security headers on the website. Deploying with these open is fine for a trial with test data, but close them before real employee data goes in.
