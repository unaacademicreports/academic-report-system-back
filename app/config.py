import os
import ssl
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from dotenv import load_dotenv

load_dotenv()

# pg8000 no entiende los parámetros de SSL estilo libpq/psycopg2 en la query
# string (p. ej. ?sslmode=require, channel_binding). El SSL se configura vía
# connect_args (ssl_context). Estos se eliminan de la URL y se traducen.
_LIBPQ_ONLY_PARAMS = {"sslmode", "channel_binding"}


def _normalize_pg_url(url: str | None) -> str | None:
    if not url:
        return None
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+pg8000://", 1)

    # Remover params que pg8000 no acepta para que no lleguen a connect().
    parts = urlsplit(url)
    if parts.query:
        kept = [(k, v) for k, v in parse_qsl(parts.query) if k not in _LIBPQ_ONLY_PARAMS]
        url = urlunsplit(parts._replace(query=urlencode(kept)))
    return url


def _wants_ssl(url: str | None) -> bool:
    """True si la URL original pedía SSL (sslmode != disable)."""
    if not url:
        return False
    query = dict(parse_qsl(urlsplit(url).query))
    return query.get("sslmode", "").lower() not in ("", "disable")


def _pg_engine_options(raw_url: str | None) -> dict:
    """Engine options para pg8000. pool_pre_ping evita errores con las
    conexiones idle que Neon cierra; SSL se añade si la URL lo pedía."""
    if not raw_url:
        return {}
    options = {
        "pool_pre_ping": True,   # descarta conexiones idle que Neon cerró
        "pool_recycle": 300,     # recicla conexiones antes del cierre por inactividad de Neon
        "pool_timeout": 30,      # no esperar indefinidamente una conexión del pool
    }
    if _wants_ssl(raw_url):
        options["connect_args"] = {"ssl_context": ssl.create_default_context()}
    return options


class BaseConfig:
    DEBUG = False
    TESTING = False

    # SQLAlchemy
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    SECRET_KEY = os.getenv("SECRET_KEY")

    # JWT settings
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
    JWT_ISSUER = os.getenv("JWT_ISSUER", "newsletter-generator-una")
    JWT_AUDIENCE = os.getenv("JWT_AUDIENCE", "newsletter-generator-una")
    JWT_EXP_HOURS = int(os.getenv("JWT_EXP_HOURS", 1))
    JWT_REFRESH_EXP_HOURS = int(os.getenv("JWT_REFRESH_EXP_HOURS", JWT_EXP_HOURS * 2))

    # URL prefix
    API_PREFIX  = os.getenv("API_PREFIX",  "/api")
    API_VERSION = os.getenv("API_VERSION", "v1")

    # File report settings
    UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "data")
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH", 16 * 1024 * 1024))
    MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", 5 * 1024 * 1024))
    ALLOWED_EXTENSIONS = set(os.getenv("ALLOWED_EXTENSIONS", "rep").split(','))


_RAW_DB_URL = os.getenv("DATABASE_URL") or os.getenv("URL_DB") or os.getenv("DB_ENGINE")


class ProductionConfig(BaseConfig):
    DEBUG = False
    TESTING = False

    # Prefer a single DATABASE_URL env var. Backwards compatible fallbacks: URL_DB or DB_ENGINE.
    DATABASE_URL = _normalize_pg_url(_RAW_DB_URL)

    SQLALCHEMY_DATABASE_URI = DATABASE_URL or ""
    SQLALCHEMY_ENGINE_OPTIONS = _pg_engine_options(_RAW_DB_URL)


class DevelopmentConfig(BaseConfig):
    DEBUG = True

    DATABASE_URL = _normalize_pg_url(_RAW_DB_URL)

    SQLALCHEMY_DATABASE_URI = DATABASE_URL or "sqlite:///dev.db"
    SQLALCHEMY_ENGINE_OPTIONS = _pg_engine_options(_RAW_DB_URL)


class TestingConfig(BaseConfig):
    TESTING = True
    SECRET_KEY = "testing-secret-key-0000"
    JWT_SECRET_KEY = "testing-jwt-secret-key-0000"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"