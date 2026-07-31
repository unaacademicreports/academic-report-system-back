import logging
import uuid
from datetime import datetime, timezone, timedelta
from functools import wraps

import jwt as pyjwt
from flask import request, g, current_app

from app.errors import api_error

logger = logging.getLogger(__name__)


def _build_token(user_payload: dict, token_type: str, exp_hours: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        **user_payload,
        'type': token_type,
        'jti': str(uuid.uuid4()),
        'iss': current_app.config['JWT_ISSUER'],
        'aud': current_app.config['JWT_AUDIENCE'],
        'iat': now,
        'exp': now + timedelta(hours=exp_hours),
    }
    return pyjwt.encode(payload, current_app.config['JWT_SECRET_KEY'], algorithm='HS256')


def _decode_token(token: str, expected_type: str | None = None) -> tuple[dict | None, object]:
    """Decode a JWT. Returns (payload, error_response) — one of them is None."""
    try:
        payload = pyjwt.decode(
            token,
            current_app.config['JWT_SECRET_KEY'],
            algorithms=['HS256'],
            issuer=current_app.config['JWT_ISSUER'],
            audience=current_app.config['JWT_AUDIENCE'],
        )
    except pyjwt.ExpiredSignatureError:
        logger.warning("Token expirado en %s %s", request.method, request.path)
        return None, api_error("TOKEN_EXPIRADO", "El token ha expirado.", http_status=401)
    except pyjwt.InvalidTokenError:
        logger.warning("Token inválido o malformado en %s %s", request.method, request.path)
        return None, api_error("TOKEN_INVALIDO", "Token inválido o malformado.", http_status=401)

    token_type = payload.get('type')
    if token_type is None:
        logger.warning("Token sin claim 'type' en %s %s", request.method, request.path)
        return None, api_error("TOKEN_INVALIDO", "Token inválido o malformado.", http_status=401)

    if expected_type is not None and token_type != expected_type:
        logger.warning(
            "Tipo de token incorrecto en %s %s: se esperaba '%s' y llegó '%s'",
            request.method, request.path, expected_type, token_type,
        )
        if expected_type == 'refresh':
            return None, api_error(
                "TOKEN_TIPO_INVALIDO",
                "Se debe enviar el refresh_token, no el access token.",
                http_status=400,
            )
        return None, api_error(
            "TOKEN_TIPO_INVALIDO",
            "El refresh token no puede usarse para autenticación.",
            http_status=401,
        )

    jti = payload.get('jti')
    if jti:
        from app.auth.models.revoked_token import RevokedToken
        if RevokedToken.is_revoked(jti):
            logger.warning(
                "Token revocado usado por %s en %s %s",
                payload.get('email'), request.method, request.path,
            )
            return None, api_error("TOKEN_REVOCADO", "El token ha sido revocado.", http_status=401)

    return payload, None


def _extract_bearer() -> str | None:
    header = request.headers.get('Authorization', '')
    if header.startswith('Bearer '):
        return header[7:]
    return None


def require_auth(f):
    """Validate Bearer JWT. Stores decoded payload in g.current_user."""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = _extract_bearer()
        if not token:
            logger.warning("Request sin token Bearer en %s %s", request.method, request.path)
            return api_error("TOKEN_REQUERIDO", "Se requiere el header Authorization: Bearer <token>.", http_status=401)

        payload, err = _decode_token(token, expected_type='access')
        if err:
            return err

        g.current_user = payload
        return f(*args, **kwargs)
    return decorated


def require_role(*roles: str):
    """Validate Bearer JWT and require at least one of the given roles."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            token = _extract_bearer()
            if not token:
                logger.warning("Request sin token Bearer en %s %s", request.method, request.path)
                return api_error("TOKEN_REQUERIDO", "Se requiere el header Authorization: Bearer <token>.", http_status=401)

            payload, err = _decode_token(token, expected_type='access')
            if err:
                return err

            g.current_user = payload
            if payload.get('role') not in roles:
                logger.warning(
                    "Rol insuficiente para %s en %s %s: tiene '%s', requiere %s",
                    payload.get('email'), request.method, request.path,
                    payload.get('role'), ' | '.join(roles),
                )
                return api_error(
                    "ACCESO_DENEGADO",
                    f"Rol requerido: {' | '.join(roles)}.",
                    http_status=403,
                )
            return f(*args, **kwargs)
        return decorated
    return decorator


def current_user_id() -> int | None:
    """Return the authenticated user's ID from the JWT payload."""
    return getattr(g, 'current_user', {}).get('sub')


def current_user_email() -> str | None:
    """Return the authenticated user's email from the JWT payload."""
    return getattr(g, 'current_user', {}).get('email')


def current_user_fullname() -> str | None:
    """Return firstname + lastname from the JWT payload as a single string."""
    payload = getattr(g, 'current_user', {})
    parts = [payload.get('firstname', ''), payload.get('lastname', '')]
    fullname = ' '.join(p for p in parts if p)
    return fullname or None


def current_user_has_role(*roles: str) -> bool:
    """Return True if the authenticated user's role is one of the given roles."""
    return getattr(g, 'current_user', {}).get('role') in roles
