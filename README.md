# CostAdvisor — Architecture Overview

A plain-English map of what runs where and how the pieces fit together.

## The flow, top to bottom

```
                        Your browser
                             │
                             ▼
            ┌─────────────────────────────────┐
            │  Cloudflare  (the front door)    │
            │  • owns the domain + DNS         │
            │  • serves the website            │
            │  • forwards API calls to Railway │
            └───────────────┬─────────────────┘
     ┌───────────────────────┼───────────────────────┬──────────────────┐
     ▼                       ▼                        ▼                  ▼
Landing (prod)         App SPA (prod)           Landing (staging)  App SPA (staging)
costadvisor.org        app.costadvisor.org      dev.costadvisor.org app.dev.costadvisor.org
Cloudflare Worker      Cloudflare Worker        Cloudflare Worker  Cloudflare Worker
"costadvisor-landing"  "costadvisor-web"        "costadvisor-landing-dev" "costadvisor-web-dev"
                              │                                          │
                              ▼                                         ▼
                                        Backend API
                              api.costadvisor.org (prod)
                              api-dev.costadvisor.org (staging)
                              Railway (FastAPI)
                                     │
                ┌──────────────────┬─────────────────┼──────────────────┐
                ▼                  ▼                  ▼                  ▼
           PostgreSQL           Redis        Background worker      Ollama (the AI)
           (Railway)          (Railway)      (Railway, Celery)      Hetzner VM,
           the database      cache + jobs     scheduled tasks       reached ONLY over
                                              (e.g. backups)        Tailscale (private)
```

## Each piece, in one line

- **Cloudflare** — the front door. Owns the `costadvisor.org` domain, serves the website, and forwards API requests to Railway.
- **Landing page** — the public marketing site (static HTML, no login). Runs on Cloudflare. Two copies: **production** (`costadvisor.org` + `www.costadvisor.org`) and **staging** (`dev.costadvisor.org`).
- **App SPA** — the actual product, behind login. A React app (built with Vite). Runs on Cloudflare, on its own `app.` subdomain so it's never confused with the marketing site. Two copies: **production** (`app.costadvisor.org`) and **staging** (`app.dev.costadvisor.org`).
- **Backend API** — the brains. FastAPI (Python) on Railway. Handles logins, data, pricing logic, and talking to the AI. Two copies: **prod** (`api.costadvisor.org`) and **staging** (`api-dev.costadvisor.org`).
- **PostgreSQL** — the database; all the real data lives here. On Railway. Separate one per environment.
- **Redis** — fast temporary storage: caching plus the queue for background jobs. On Railway.
- **Background worker (Celery)** — does slow/scheduled work outside the API (e.g. the nightly backup). On Railway.
- **Ollama (the AI)** — the language model. Runs on a private server at Hetzner, reachable only over **Tailscale** (a private network), so it's never exposed to the internet. Shared by prod and staging.
- **Google** — how users log in (Google sign-in / OAuth).
- **Backblaze B2** — off-site backups of the database.

## Code and how it deploys

All code lives in one GitHub repo: **github.com/costadvisor/CostAdvisor**

- `frontend/` — the React app
- `backend/` — the FastAPI app + the background worker

Deploys are automatic on push:

- **`main` branch → production** — pushing to `main` rebuilds and deploys the prod frontend (Cloudflare) and prod API (Railway).
- **`dev` branch → staging** — pushing to `dev` rebuilds and deploys the staging frontend and staging API.

The frontend is compiled with `npm run build`; the compiled output in `frontend/dist` is what Cloudflare actually serves.

## Two environments, same shape

|                 | Production                | Staging                     |
|-----------------|---------------------------|------------------------------|
| Landing page    | costadvisor.org (+ www)   | dev.costadvisor.org          |
| App             | app.costadvisor.org       | app.dev.costadvisor.org      |
| API             | api.costadvisor.org       | api-dev.costadvisor.org      |
| Git branch      | `main`                  | `dev`                     |
| Database / Redis| their own (Railway)     | their own (Railway)       |
| AI model        | shared Hetzner Ollama   | the same shared box       |
