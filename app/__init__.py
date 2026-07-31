import logging
import os

from flask import Flask

from .extensions import cors, db, limiter
from .config import ProductionConfig, DevelopmentConfig, TestingConfig
from .errors import register_error_handlers, api_success, api_error
from .logging_setup import init_logging, register_request_logging


def _validate_db_connection(app):
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError, ProgrammingError

    _logger = logging.getLogger(__name__)
    try:
        with app.app_context():
            # pg8000 no activa 'insertmanyvalues' en INSERTs sin RETURNING: cae a
            # executemany clásico (un round-trip por fila), lo que vuelve la ingesta
            # masiva inviable contra una DB remota. Este flag fuerza el INSERT
            # multi-fila (una sola sentencia). No afecta a SQLite.
            if db.engine.dialect.name == 'postgresql':
                db.engine.dialect.use_insertmanyvalues_wo_returning = True
            db.session.execute(text("SELECT 1"))
        _logger.info("Conexión a la base de datos establecida correctamente.")
    except (OperationalError, ProgrammingError) as e:
        _logger.critical("No se pudo conectar a la base de datos: %s", e)
        raise RuntimeError(
            "No se pudo conectar a la base de datos. "
            "Verifique DATABASE_URL, credenciales y que el servidor esté disponible."
        ) from e


def _validate_secrets(app):
    weak = {'secret_key', 'jwt_secret_key', ''}
    secret = app.config.get('SECRET_KEY', '')
    jwt_secret = app.config.get('JWT_SECRET_KEY', '')
    if not secret or secret in weak or len(secret) < 16:
        raise RuntimeError(
            "SECRET_KEY must be a strong, unique value (min 16 chars). "
            "Set it in your .env file."
        )
    if not jwt_secret or jwt_secret in weak or len(jwt_secret) < 16:
        raise RuntimeError(
            "JWT_SECRET_KEY must be a strong, unique value (min 16 chars). "
            "Set it in your .env file."
        )


def create_app():
    init_logging()

    app = Flask(__name__)

    env = os.getenv('FLASK_ENV', 'development')

    if env == 'development':
        _db_url = DevelopmentConfig.DATABASE_URL
        if not _db_url:
            raise RuntimeError("DATABASE_URL environment variable is required in Development")
        if not _db_url.startswith("postgresql"):
            raise RuntimeError("DATABASE_URL must be a PostgreSQL URI (postgresql://...)")
        app.config.from_object(DevelopmentConfig)
        _validate_secrets(app)
    elif env == 'production':
        _db_url = ProductionConfig.DATABASE_URL
        if not _db_url:
            raise RuntimeError("DATABASE_URL environment variable is required in Production")
        if not _db_url.startswith("postgresql"):
            raise RuntimeError("DATABASE_URL must be a PostgreSQL URI (postgresql://...)")
        app.config.from_object(ProductionConfig)
        _validate_secrets(app)
    elif env == 'testing':
        app.config.from_object(TestingConfig)
    else:
        raise RuntimeError(
            f"FLASK_ENV desconocido: '{env}'. "
            "Valores válidos: development, production, testing."
        )

    from werkzeug.middleware.proxy_fix import ProxyFix
    proxy_hops = int(os.getenv('PROXY_FORWARDED_HOPS', '1'))
    if proxy_hops > 0:
        app.wsgi_app = ProxyFix(
            app.wsgi_app, x_for=proxy_hops, x_proto=proxy_hops, x_host=proxy_hops
        )

    cors_origins = os.getenv('CORS_ORIGINS')
    if cors_origins:
        cors.init_app(app, origins=[o.strip() for o in cors_origins.split(',') if o.strip()])
    else:
        cors.init_app(app)

    limiter.init_app(app)

    db.init_app(app)
    _validate_db_connection(app)
    from app.auth.models import User, Role
    from app.auth.models.revoked_token import RevokedToken
    from app.models import Major, Student, Subject, Grade

    from .blueprints import register_blueprints
    register_blueprints(app)

    from app.auth import login_manager
    login_manager.init_app(app)

    register_request_logging(app)
    register_error_handlers(app)

    @app.route("/")
    def index():
        return api_success(
            data={"app": "Newsletter Generator UNA", "version": "1.0"},
            mensaje="API operativa.",
        )

    @app.route(f"{app.config['API_PREFIX']}/{app.config['API_VERSION']}/status")
    def status():
        from sqlalchemy import text
        from sqlalchemy.exc import SQLAlchemyError

        try:
            db.session.execute(text("SELECT 1"))
        except SQLAlchemyError:
            logging.getLogger(__name__).exception(
                "Health check: fallo de conexión a la base de datos"
            )
            return api_error(
                "DB_DOWN",
                "La base de datos no está disponible",
                http_status=503,
            )
        return api_success(data={"app": "up", "database": "up"}, mensaje="OK")

    return app
