import logging
import os
import sys
import time
import uuid

from flask import g, has_request_context, request

logger = logging.getLogger(__name__)

_LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(request_id)s] %(name)s: %(message)s"


class RequestContextFilter(logging.Filter):
    def filter(self, record):
        if has_request_context():
            record.request_id = getattr(g, 'request_id', '-')
        else:
            record.request_id = '-'
        return True


def init_logging():
    root = logging.getLogger()
    if any(getattr(h, '_app_log_handler', False) for h in root.handlers):
        return

    level_name = os.getenv('LOG_LEVEL', 'INFO').upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    handler.addFilter(RequestContextFilter())
    handler._app_log_handler = True

    root.setLevel(level)
    root.addHandler(handler)


def register_request_logging(app):
    status_path = f"{app.config['API_PREFIX']}/{app.config['API_VERSION']}/status"
    health_paths = {'/', status_path}

    def _level_for(path):
        return logging.DEBUG if path in health_paths else logging.INFO

    @app.before_request
    def _log_request_start():
        g.request_id = request.headers.get('X-Request-ID') or uuid.uuid4().hex[:8]
        g._request_start = time.perf_counter()
        logger.log(_level_for(request.path), "--> %s %s", request.method, request.path)

    @app.after_request
    def _log_request_end(response):
        duration_ms = (time.perf_counter() - getattr(g, '_request_start', time.perf_counter())) * 1000
        user = getattr(g, 'current_user', {}).get('email') or 'anon'
        logger.log(
            _level_for(request.path),
            "<-- %s %s -> %d (%.0fms) user=%s",
            request.method, request.path, response.status_code, duration_ms, user,
        )
        response.headers['X-Request-ID'] = getattr(g, 'request_id', '-')
        return response
