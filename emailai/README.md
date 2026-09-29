# AI Email Processing Pipeline

A production-grade Django + Celery system that ingests emails via provider
webhooks, strips PII before anything touches an external API, classifies
intent/sentiment locally, and uses Gemini to draft structured action items
and a reply — then restores real names/contacts before the draft reaches
a human.

```
Provider (SES/Mailgun/SendGrid)
        │  HTTP POST, HMAC-signed
        ▼
 ingestion/views.py  ── verify signature, write EmailRecord, dispatch PK
        │
        ▼  Celery chain
 processing/tasks.py
   1. anonymize_email            (cpu_heavy_queue)  Presidio → masked_body
   2. classify_intent_and_sentiment (cpu_heavy_queue) DistilBERT + RoBERTa
   3. generate_reply_with_gemini (api_bound_queue)  Gemini structured JSON
        │
        ▼
 Postgres: EmailRecord, EmailMetadata, ActionItem, GeneratedReply
```

## Project layout

| Path | Phase | Purpose |
|---|---|---|
| `ingestion/` | 1 | Webhook view, HMAC verification, payload normalization |
| `core/models.py` | 2 | `EmailRecord`, `EmailMetadata`, `ActionItem`, `GeneratedReply` |
| `config/celery.py`, `config/settings.py` | 3 | Queue routing, worker tuning |
| `processing/anonymizer.py` | 4 | Presidio engines + `StatefulReversibleAnonymizer` |
| `processing/nlp_intent.py`, `nlp_sentiment.py`, `priority.py` | 5 | ONNX DistilBERT, RoBERTa, priority matrix |
| `processing/llm_gemini.py`, `schemas.py` | 6 | Gemini structured extraction + reply drafting |
| `processing/tasks.py` | — | Orchestrates the chain across all three queues |
| `frontend/` | 7 | Server-rendered dashboard UI — plain HTML/CSS/JS templates only, no frontend framework or build step |

## Frontend dashboard (Phase 7)

A staff-facing UI at `/`, built entirely with Django templates: no React,
no bundler, no npm — just HTML rendered server-side, CSS embedded in
`frontend/templates/frontend/base.html`, and vanilla JS `fetch()` calls
for the two interactive actions (toggling an action item, editing/approving
a draft reply). All templates live under `frontend/templates/frontend/`.

- **`/`** — inbox: stats (total / priority / pending drafts / still
  processing), filter by priority flag or intent category
- **`/email/<id>/`** — detail view: original body, intent + sentiment +
  priority badges, checkable action items, an editable reply draft with
  "save" / "approve & save" buttons, and a collapsible PII de-anonymization
  map for audit purposes
- Auth reuses Django's built-in admin login (`LOGIN_URL = /admin/login/`)
  — every dashboard view is `@login_required`. Create a user via
  `python manage.py createsuperuser` to get in; there's no separate
  signup/auth UI built here since none was asked for.
- The two mutating endpoints (`toggle-action-item`, `update-reply`) are
  CSRF-protected `POST`-only JSON endpoints called via the shared
  `apiPost()` helper in `base.html`.

## Setup

This codebase was written and syntax-validated in a sandboxed environment
**without network access**, so it has not been run end-to-end against a
live Postgres/Redis/Gemini stack. Follow these steps in your own
environment to bring it up:

### 1. System dependencies

```bash
# Postgres and Redis must be running and reachable
createdb emailai
redis-server &
```

### 2. Python environment

```bash
cd emailai
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m spacy download en_core_web_sm
```

### 3. Configure environment

```bash
cp .env.example .env
# Fill in: EJF_ENCRYPTION_KEYS, INBOUND_WEBHOOK_SIGNING_KEY, GEMINI_API_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 4. Export the intent model to quantized ONNX (one-time, offline)

See the docstring at the top of `processing/nlp_intent.py` for the exact
`optimum` export + quantization snippet. Fine-tune `distilbert-base-uncased`
on your own labeled intent data first — the checkpoint referenced there is
a starting point, not a ready-made classifier.

### 5. Migrate and run

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

### 6. Start the three Celery worker fleets (separate terminals/processes)

```bash
celery -A config worker -Q io_fast_queue -P gevent -c 200 -n io_worker@%h
celery -A config worker -Q cpu_heavy_queue -P prefork -c 4 -n cpu_worker@%h
celery -A config worker -Q api_bound_queue -P gevent -c 100 -n api_worker@%h
```

### 7. Point your provider at the webhook

```
POST https://your-domain.com/webhooks/inbound-email/
```

Configure Mailgun/SendGrid/SES to send inbound parse events here, using
the same signing key as `INBOUND_WEBHOOK_SIGNING_KEY`.

## Known gaps to close before production

- **SES/SNS verification is stubbed.** `ingestion/security.py` raises on
  `provider=ses` — SNS signs with RSA over a certificate URL, not HMAC.
  Implement certificate fetch/caching + RSA verification before using SES.
- **The intent classifier ships untrained.** `nlp_intent.py` exports base
  `distilbert-base-uncased`; fine-tune it on your own labeled intent
  categories (billing, payment_failure, etc.) before relying on its output.
- **No test suite yet.** Add unit tests per app (`ingestion/tests.py`,
  `processing/tests.py`) covering signature verification, the anonymizer's
  round-trip (mask → restore), and priority-matrix edge cases.
- **No deployment config yet** (Dockerfile, gunicorn/nginx, Celery
  supervision via systemd or a process manager) — flagged in the original
  brief as a later phase.
