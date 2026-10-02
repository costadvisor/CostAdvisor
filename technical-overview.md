# CostAdvisor — Technical Overview

B2B SaaS platform for procurement cost intelligence: buyer-side "should-cost"
modeling for companies buying commodity-linked industrial products (chemicals,
metals, etc.). The platform decomposes a product into raw-material cost
components, ties each component to live commodity/FX index data, and computes
what a product should cost today versus what the supplier is actually
charging — giving procurement teams a defensible negotiation position instead
of gut feel.

## Stack

**Backend**
- Python, FastAPI (REST API)
- SQLAlchemy (ORM), Alembic (schema migrations)
- PostgreSQL — multi-tenant via Postgres Row-Level Security (tenant isolation
  enforced at the database level, not just filtered in application code)
- Celery + Redis for background jobs (nightly data scrapers, alerts,
  scheduled recomputes)

**Frontend**
- React 18, React Router 6, Vite
- Custom CSS-variable-based design system (no Tailwind / component library)

**Auth**
- Google OAuth 2.0 (PKCE flow)
- JWT in HttpOnly cookies, rotating refresh tokens

**AI**
- Self-hosted LLM (Ollama, Llama 3.1 8B) for generated negotiation narratives,
  reachable only over a private network

**Infrastructure**
- Railway — API + PostgreSQL + Redis
- Cloudflare Workers — React SPA + a separate static marketing site
- Two parallel environments (staging / production), GitHub-based workflow

**Monitoring & QA**
- Sentry (error tracking)
- pytest (600+ backend tests)

## What a candidate should be comfortable with

- Python with a modern async web framework (FastAPI, or equivalent
  Django/Flask experience)
- Relational database design and SQL
- REST API design
- React and component-based frontend development
- Git-based team workflows

**Bonus:** multi-tenant SaaS architecture patterns, role-based access
control (RBAC), OAuth 2.0 flows.
