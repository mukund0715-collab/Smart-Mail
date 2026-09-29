"""
Django settings for the AI Email Processing Pipeline.

Environment variables are read via django-environ from a `.env` file at the
project root (see .env.example for the full list of required variables).
"""
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-insecure-key-change-me")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

# --------------------------------------------------------------------------
# Applications
# --------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "encrypted_json_fields",
    "core",
    "ingestion",
    "processing",
    "frontend",
    'django_mailbox',
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --------------------------------------------------------------------------
# Database (PostgreSQL — required for pgcrypto / JSONField support)
# --------------------------------------------------------------------------
DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://postgres:1234@127.0.0.1:8001/emailai",
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------
# Application-level encryption (Phase 2)
# django-encrypted-json-fields: encrypts raw_body / masked_body at rest.
# Keys are Fernet keys, comma-separated for rotation support (newest first).
# --------------------------------------------------------------------------
EJF_ENCRYPTION_KEYS = env.list("EJF_ENCRYPTION_KEYS", default=[])
EJF_UNIQUE_BY_HASH = True  # store an indexable hash alongside encrypted value


IMAP_HOST = env("IMAP_HOST", default="imap.gmail.com")
IMAP_USER = env("IMAP_USER", default="")
IMAP_PASSWORD = env("IMAP_PASSWORD", default="")

# --------------------------------------------------------------------------
# Celery — Phase 3
# --------------------------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://localhost:6379/1")

CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE

# Reliability: don't ack until the task finishes, so a killed/OOM worker
# re-delivers the job instead of silently losing it.
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True

# Fair dispatch: don't let one worker hoard tasks from the queue.
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

# Recycle worker child processes periodically to bound memory growth from
# native extensions (spaCy / torch / onnxruntime) that don't always release
# memory cleanly back to the OS.
CELERY_WORKER_MAX_TASKS_PER_CHILD = 1000

CELERY_BROKER_TRANSPORT_OPTIONS = {
    "visibility_timeout": 3600,
    "queue_order_strategy": "priority",
}

# Dedicated queues by workload shape (Phase 3):
#   io_fast_queue    -> ingestion / lightweight DB writes (gevent pool)
#   cpu_heavy_queue   -> Presidio + DistilBERT/RoBERTa inference (prefork pool)
#   api_bound_queue   -> outbound Gemini API calls (gevent/eventlet pool)
CELERY_TASK_ROUTES = {
    "ingestion.views.fetch_unseen_imap_emails": {"queue": "io_fast_queue"},
    "processing.tasks.ingest_and_persist": {"queue": "io_fast_queue"},
    "processing.tasks.process_email_pipeline": {"queue": "io_fast_queue"},
    "processing.tasks.anonymize_email": {"queue": "cpu_heavy_queue"},
    "processing.tasks.classify_intent_and_sentiment": {"queue": "cpu_heavy_queue"},
    "processing.tasks.generate_reply_with_gemini": {"queue": "api_bound_queue"},
}

from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    "poll-imap-every-minute": {
        "task": "ingestion.views.fetch_unseen_imap_emails",
        "schedule": 60.0,  # runs every 60 seconds
    },
}

# --------------------------------------------------------------------------
# NLP / ML model configuration — Phase 4 & 5
# --------------------------------------------------------------------------
SPACY_MODEL_NAME = env("SPACY_MODEL_NAME", default="en_core_web_sm")
INTENT_MODEL_NAME = env("INTENT_MODEL_NAME", default="distilbert-base-uncased")
INTENT_ONNX_DIR = env("INTENT_ONNX_DIR", default=str(BASE_DIR / "ml_artifacts" / "intent_onnx"))
SENTIMENT_MODEL_NAME = env(
    "SENTIMENT_MODEL_NAME", default="cardiffnlp/twitter-roberta-base-sentiment-latest"
)

# --------------------------------------------------------------------------
# Gemini (Google GenAI SDK) — Phase 6
# --------------------------------------------------------------------------
GEMINI_API_KEY = env("GEMINI_API_KEY", default="")
GEMINI_MODEL = env("GEMINI_MODEL", default="gemini-2.5-flash")
GEMINI_TEMPERATURE = 0.2

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
}

# --------------------------------------------------------------------------
# Frontend dashboard auth
# Reuses Django's built-in admin login rather than a standalone auth UI —
# create staff users via `createsuperuser` / admin to access the dashboard.
# --------------------------------------------------------------------------
LOGIN_URL = "/admin/login/"
LOGIN_REDIRECT_URL = "/"

# --------------------------------------------------------------------------
# Outbound SMTP Configuration 
# --------------------------------------------------------------------------
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = env("EMAIL_HOST", default="smtp.gmail.com")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default=EMAIL_HOST_USER)