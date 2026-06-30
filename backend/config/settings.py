"""
Django settings for the Rhombus platform.

All sensitive values are read from environment variables (populated by
docker-compose via .env).  This file never hard-codes secrets.
"""
import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

# ── Security ──────────────────────────────────────────────────────────────────
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
DEBUG = os.getenv("DJANGO_DEBUG", "False").lower() in ("true", "1", "yes")
ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "localhost").split(",")

# ── Application ───────────────────────────────────────────────────────────────
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "rest_framework",
    "corsheaders",
    # Local
    "jobs",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",  # must be first
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
            ]
        },
    }
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ── Database ──────────────────────────────────────────────────────────────────
# dj_database_url parses DATABASE_URL=postgres://user:pass@host:port/db
DATABASES = {
    "default": dj_database_url.config(
        env="DATABASE_URL",
        default="postgres://rhombus:rhombus@db:5432/rhombus",
        conn_max_age=60,
    )
}

# ── Celery ────────────────────────────────────────────────────────────────────
# Celery reads CELERY_BROKER_URL / CELERY_RESULT_BACKEND directly from the
# environment; we also expose them here so Django's test runner can override
# them in-process if needed.
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/0")
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "UTC"
CELERY_ENABLE_UTC = True
# Store task metadata (state, result) for 24 hours so the UI can still
# show historical job info after the worker finishes.
CELERY_RESULT_EXPIRES = 86400
# Suppress Celery 6.0 pending-deprecation warning about broker retry on startup.
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

# Explicitly import the task module so Celery's worker registers process_job
# at startup.  autodiscover_tasks(["tasks"]) would look for tasks/tasks.py,
# which does not exist — our task lives in tasks/pipeline.py.
CELERY_IMPORTS = ("tasks.pipeline",)

# ── Redis cache (for LLM regex results) ──────────────────────────────────────
REDIS_CACHE_URL = os.environ.get("REDIS_CACHE_URL", "redis://redis:6379/1")
REGEX_CACHE_TTL_SECONDS = int(os.getenv("REGEX_CACHE_TTL_SECONDS", "3600"))

# ── File storage ──────────────────────────────────────────────────────────────
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(BASE_DIR / "uploads")))
RESULT_DIR = Path(os.getenv("RESULT_DIR", str(BASE_DIR / "results")))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
RESULT_DIR.mkdir(parents=True, exist_ok=True)

# Keep in-memory upload threshold low (2 MB) so large files are written to a
# temp file immediately — FILE_UPLOAD_MAX_MEMORY_SIZE controls the file/memory
# boundary, DATA_UPLOAD_MAX_MEMORY_SIZE controls non-file POST body size.
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024   # 2 MB → files go to disk fast
DATA_UPLOAD_MAX_MEMORY_SIZE = 1 * 1024 * 1024   # 1 MB for non-file POST fields

# ── LLM ───────────────────────────────────────────────────────────────────────
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "30"))

# ── REST framework ────────────────────────────────────────────────────────────
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.MultiPartParser",  # file uploads
        "rest_framework.parsers.JSONParser",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 100,
}

# ── CORS ──────────────────────────────────────────────────────────────────────
CORS_ALLOWED_ORIGINS = os.getenv(
    "CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
).split(",")

# ── Internationalisation ──────────────────────────────────────────────────────
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# ── Static files ──────────────────────────────────────────────────────────────
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
