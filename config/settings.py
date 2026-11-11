"""Django settings for DocIntel Lab. Everything environment-specific comes from .env."""
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-only-insecure-key")
DEBUG = env.bool("DJANGO_DEBUG", default=True)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_htmx",
    "core",
    "apps.ingest",
    "apps.prompts",
    "apps.rag",
    "apps.kg",
    "apps.rules",
    "apps.qa_gen",
    "apps.codegen",
    "apps.agents",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    # Hostnames are the docker-compose service names. The app runs in Docker.
    "default": env.db("DATABASE_URL", default="postgres://docintel:docintel@postgres:5432/docintel"),
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
MEDIA_URL = "media/"
MEDIA_ROOT = Path(env("MEDIA_ROOT", default=str(BASE_DIR / "data" / "media")))
CORPUS_DIR = BASE_DIR / "data" / "corpus"
EVAL_RESULTS_DIR = BASE_DIR / "evals" / "results"
DATA_UPLOAD_MAX_MEMORY_SIZE = 80 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 80 * 1024 * 1024

# --- Celery -----------------------------------------------------------------
CELERY_BROKER_URL = env("REDIS_URL", default="redis://redis:6379/0")
CELERY_RESULT_BACKEND = env("REDIS_URL", default="redis://redis:6379/0")
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=False)
CELERY_TASK_EAGER_PROPAGATES = False
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

# --- LLM / embeddings ---------------------------------------------------------
LLM_PROVIDER = env("LLM_PROVIDER", default="mock").strip().lower()  # anthropic | openai | google | mock
LLM_MODEL = env("LLM_MODEL", default="").strip()
LLM_TEMPERATURE = env.float("LLM_TEMPERATURE", default=0.0)
LLM_MAX_RETRIES = env.int("LLM_MAX_RETRIES", default=2)
# Provider 429s: exponential backoff (base * 2^n, capped), honouring the provider's retry hint.
LLM_RATE_LIMIT_RETRIES = env.int("LLM_RATE_LIMIT_RETRIES", default=6)
LLM_BACKOFF_BASE_SECONDS = env.float("LLM_BACKOFF_BASE_SECONDS", default=4.0)
LLM_BACKOFF_MAX_SECONDS = env.float("LLM_BACKOFF_MAX_SECONDS", default=90.0)
# Calls in flight at once across web, worker and commands (Redis semaphore). 0 = unlimited.
LLM_MAX_CONCURRENCY = env.int("LLM_MAX_CONCURRENCY", default=2)
LLM_SLOT_TIMEOUT_SECONDS = env.int("LLM_SLOT_TIMEOUT_SECONDS", default=300)
LLM_SLOT_WAIT_SECONDS = env.int("LLM_SLOT_WAIT_SECONDS", default=900)
# Check the provider key and model when Django starts. Skipped under pytest.
LLM_VALIDATE_ON_STARTUP = env.bool("LLM_VALIDATE_ON_STARTUP", default=True)

EMBEDDINGS_PROVIDER = env("EMBEDDINGS_PROVIDER", default="mock")  # local | openai | mock
EMBEDDINGS_MODEL = env("EMBEDDINGS_MODEL", default="")

# Cross-encoder rerank. Off by default: the hybrid+rerank mode uses a lexical
# rerank unless this is true AND the image was built with INSTALL_ML=1.
RERANK_ENABLED = env.bool("RERANK_ENABLED", default=False)
RERANK_MODEL = env("RERANK_MODEL", default="cross-encoder/ms-marco-MiniLM-L-6-v2")
RAG_MIN_VECTOR_SIMILARITY = env.float("RAG_MIN_VECTOR_SIMILARITY", default=0.25)

# --- Knowledge graph ------------------------------------------------------------
GRAPH_BACKEND = env("GRAPH_BACKEND", default="neo4j")  # neo4j | networkx
NEO4J_URI = env("NEO4J_URI", default="bolt://neo4j:7687")
NEO4J_USER = env("NEO4J_USER", default="neo4j")
NEO4J_PASSWORD = env("NEO4J_PASSWORD", default="docintel-neo4j")
KG_PROMPT_VERSION = env("KG_PROMPT_VERSION", default="v2")
# Extract entities and rebuild the graph when an ingest run finishes.
KG_AUTO_BUILD = env.bool("KG_AUTO_BUILD", default=True)
KG_ENTITY_MIN_SIMILARITY = env.float("KG_ENTITY_MIN_SIMILARITY", default=0.55)

# --- Rules ------------------------------------------------------------------------
RULES_PROMPT_VERSION = env("RULES_PROMPT_VERSION", default="v2")
# Near-duplicate merge: cosine of "condition -> action" at or above this, with the same numbers.
RULES_MERGE_SIMILARITY = env.float("RULES_MERGE_SIMILARITY", default=0.9)
# Conflict candidates: rules sharing an identifier, or conditions at or above this cosine.
RULES_CONFLICT_SIMILARITY = env.float("RULES_CONFLICT_SIMILARITY", default=0.6)

# --- Q&A generation (Module 6) -------------------------------------------------------
QA_PROMPT_VERSION = env("QA_PROMPT_VERSION", default="v2")
QA_JUDGE_PROMPT_VERSION = env("QA_JUDGE_PROMPT_VERSION", default="v2")
# Pairs scored below this by the groundedness judge are dropped.
QA_MIN_GROUNDEDNESS = env.float("QA_MIN_GROUNDEDNESS", default=0.8)
# Chunk pairs linked through a shared graph entity, used for multi-hop questions.
QA_MAX_PAIRS = env.int("QA_MAX_PAIRS", default=8)
# The Search evaluation samples at most this many kept pairs per question type.
QA_EVAL_MAX_PER_TYPE = env.int("QA_EVAL_MAX_PER_TYPE", default=8)
QA_EVAL_MODES = env.list("QA_EVAL_MODES", default=["vector", "bm25", "hybrid_rerank"])

# --- Rules to code (Module 7) ---------------------------------------------------------
CODEGEN_MAX_ATTEMPTS = env.int("CODEGEN_MAX_ATTEMPTS", default=3)
CODEGEN_TEST_TIMEOUT_SECONDS = env.int("CODEGEN_TEST_TIMEOUT_SECONDS", default=30)

# --- Compliance agent ---------------------------------------------------------------
# hybrid_rerank tied bm25 for the best hit@1 and MRR on the real eval_rag run (hit@5 was 1.0 for every mode).
AGENT_RETRIEVAL_MODE = env("AGENT_RETRIEVAL_MODE", default="hybrid_rerank")
AGENT_TOP_K = env.int("AGENT_TOP_K", default=6)
# Re-retrievals after a failed citation check. The answer then goes to approval with a warning.
AGENT_MAX_LOOPS = env.int("AGENT_MAX_LOOPS", default=2)

# --- Ingest pipeline ----------------------------------------------------------
INGEST_MAX_RETRIES = env.int("INGEST_MAX_RETRIES", default=2)
INGEST_CHUNK_MAX_TOKENS = env.int("INGEST_CHUNK_MAX_TOKENS", default=300)
INGEST_CHUNK_MIN_TOKENS = env.int("INGEST_CHUNK_MIN_TOKENS", default=40)
# Demo switch: the named stage fails on its first attempt so the retry path is visible.
INGEST_CHAOS_STAGE = env("INGEST_CHAOS_STAGE", default="")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "loggers": {
        "apps": {"handlers": ["console"], "level": "INFO"},
        "core": {"handlers": ["console"], "level": "INFO"},
    },
}
