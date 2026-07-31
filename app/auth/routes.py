import logging
import re
from datetime import datetime, timezone

from flask import request, current_app, g
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import check_password_hash, generate_password_hash

from app.errors import api_error, api_success
from app.extensions import db, limiter
from app.auth import auth_bp as auth
from app.auth.jwt_utils import (
    require_auth,
    require_role,
    current_user_id,
    current_user_email,
    current_user_fullname,
    current_user_has_role,
    _build_token,
    _decode_token,
)
from app.models import UserAudit
from .models.user import User
from .models.role import Role

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')

# Hash de sacrificio para igualar el tiempo de respuesta cuando el username
# no existe (evita enumeración de usuarios por timing).
_DUMMY_PASSWORD_HASH = generate_password_hash("timing-equalizer-not-a-password")


def _audit_user(username, operation, email=None, fullname=None, affected_data=None):
    db.session.add(UserAudit(
        user_username=username,
        operation=operation,
        user_email=email,
        user_fullname=fullname,
        actor_email=current_user_email(),
        actor_fullname=current_user_fullname(),
        operation_at=datetime.now(timezone.utc),
        ip_address=request.remote_addr,
        affected_data=affected_data,
    ))


def _describe_changes(before, after):
    labels = {
        'firstname': 'nombre',
        'lastname': 'apellido',
        'email': 'correo',
        'phone': 'teléfono',
        'role': 'rol',
    }
    cambios = []
    for field, label in labels.items():
        old, new = before.get(field), after.get(field)
        if old != new:
            cambios.append(f"Modificó {label}: antes='{old}' después='{new}'")
    return cambios


def _validate_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email.strip()))


def _validate_password(password: str) -> tuple[bool, str]:
    if len(password) < 8:
        return False, "La contraseña debe tener al menos 8 caracteres."
    if not any(c.isupper() for c in password):
        return False, "La contraseña debe contener al menos una letra mayúscula."
    if not any(c.isdigit() for c in password):
        return False, "La contraseña debe contener al menos un número."
    return True, ""


def _parse_page_size():
    """Lee page/size de la query string. Returns (page, size, error_response)."""
    try:
        page = int(request.args.get('page', '1'))
        if page < 1:
            raise ValueError
    except ValueError:
        return None, None, api_error("PARAM_INVALIDO", "'page' debe ser un entero >= 1.", campo="page")

    try:
        size = int(request.args.get('size', '30'))
        if size < 1:
            raise ValueError
    except ValueError:
        return None, None, api_error("PARAM_INVALIDO", "'size' debe ser un entero >= 1.", campo="size")

    return page, size, None


def _pagination_meta(page: int, size: int, total: int, count: int) -> dict:
    """Metadatos de paginación. 'per_page' es la cantidad de registros de esta página."""
    pages = (total + size - 1) // size if total else 0
    return {
        "page": page,
        "pages": pages,
        "per_page": count,
        "total": total,
        "has_prev": page > 1,
        "has_next": page < pages,
    }


###
#
# ✅ LOGIN — público
#
###
@auth.route("/auth/login", methods=['POST'])
@limiter.limit("5 per minute")
def user_login():
    data = request.get_json()
    if not data or not data.get('username') or not data.get('password'):
        return api_error("CAMPO_REQUERIDO", "username y password son requeridos.")

    user = User.query.filter_by(username=data['username']).first()
    if not user:
        check_password_hash(_DUMMY_PASSWORD_HASH, data['password'])
        logger.warning("Login fallido para username '%s': credenciales inválidas", data['username'])
        return api_error("CREDENCIALES_INVALIDAS", "Credenciales inválidas.", http_status=401)

    if not user.check_password(data['password']) or not user.is_active:
        logger.warning("Login fallido para username '%s': credenciales inválidas", data['username'])
        return api_error("CREDENCIALES_INVALIDAS", "Credenciales inválidas.", http_status=401)

    user_payload = {
        'sub': user.id,
        'email': user.email,
        'firstname': user.firstname,
        'lastname': user.lastname,
        'role': user.role.name if user.role else None,
    }
    token = _build_token(user_payload, 'access', current_app.config['JWT_EXP_HOURS'])
    refresh_token = _build_token(user_payload, 'refresh', current_app.config['JWT_REFRESH_EXP_HOURS'])

    logger.info("Login exitoso: %s (rol=%s)", user.email, user_payload['role'])
    return api_success(data={"token": token, "refresh_token": refresh_token}, mensaje="Login exitoso.")


###
#
# ✅ REFRESH TOKEN — público
#
###
@auth.route("/auth/refresh-token", methods=['POST'])
@limiter.limit("10 per minute")
def token_refresh():
    from app.auth.models.revoked_token import RevokedToken

    data = request.get_json()
    if not data or not data.get('refresh_token'):
        return api_error("CAMPO_REQUERIDO", "El campo 'refresh_token' es requerido.", campo="refresh_token")

    payload, err = _decode_token(data['refresh_token'], expected_type='refresh')
    if err:
        return err

    # Revalidar contra la BD: el usuario pudo ser eliminado, desactivado o
    # cambiar de rol después de emitido el refresh token.
    user = db.session.get(User, payload.get('sub'))
    if not user or not user.is_active:
        logger.warning("Refresh rechazado: usuario %s inexistente o inactivo", payload.get('sub'))
        return api_error("TOKEN_INVALIDO", "Token inválido o malformado.", http_status=401)

    # Rotación: el refresh token usado queda revocado y no puede reutilizarse.
    if payload.get('jti'):
        RevokedToken.revoke(payload['jti'])

    user_payload = {
        'sub': user.id,
        'email': user.email,
        'firstname': user.firstname,
        'lastname': user.lastname,
        'role': user.role.name if user.role else None,
    }
    token = _build_token(user_payload, 'access', current_app.config['JWT_EXP_HOURS'])
    refresh_token = _build_token(user_payload, 'refresh', current_app.config['JWT_REFRESH_EXP_HOURS'])

    logger.info("Token renovado para %s", user.email)
    return api_success(data={"token": token, "refresh_token": refresh_token}, mensaje="Token renovado exitosamente.")


###
#
# ✅ LOGOUT — requiere auth
#
###
@auth.route("/auth/logout", methods=['POST'])
@require_auth
def user_logout():
    from app.auth.models.revoked_token import RevokedToken

    jti = g.current_user.get('jti')
    if not jti:
        logger.warning("Logout con access token sin 'jti' de %s", current_user_email())
        return api_error("TOKEN_INVALIDO", "Token inválido o malformado.", http_status=401)
    RevokedToken.revoke(jti)

    # Revocar también el refresh token si el cliente lo envía en el body.
    data = request.get_json(silent=True) or {}
    if data.get('refresh_token'):
        payload, err = _decode_token(data['refresh_token'], expected_type='refresh')
        if not err and payload.get('jti'):
            RevokedToken.revoke(payload['jti'])

    logger.info("Logout de %s", current_user_email())
    return api_success(mensaje="Sesión cerrada correctamente.")


###
#
# ✅ CREATE NEW USER — Admin
#
###
@auth.route("/auth/user", methods=['POST'])
@require_role('Admin')
def user_create():
    data = request.get_json()
    if not data:
        return api_error("CUERPO_REQUERIDO", "El cuerpo de la solicitud es requerido.")

    required = ['firstname', 'lastname', 'username', 'email', 'password']
    for field in required:
        if not data.get(field):
            return api_error("CAMPO_REQUERIDO", f"El campo '{field}' es requerido.", campo=field)

    if not _validate_email(data['email']):
        return api_error("EMAIL_INVALIDO", "El formato del email no es válido.", campo="email")

    valid, msg = _validate_password(data['password'])
    if not valid:
        return api_error("PASSWORD_DEBIL", msg, campo="password")

    role_name = (data.get('role') or 'Viewer').strip()
    role = Role.query.filter_by(name=role_name).first()
    if not role:
        return api_error("ROL_NO_ENCONTRADO", "El rol no existe.", http_status=404, campo="role")

    try:
        new_user = User(
            firstname=data['firstname'],
            lastname=data['lastname'],
            username=data['username'],
            email=data['email'],
            phone=data.get('phone'),
        )
        new_user.set_password(data['password'])
        new_user.set_role(role)

        db.session.add(new_user)
        fullname = f"{new_user.firstname} {new_user.lastname}"
        _audit_user(new_user.username, 'CREACIÓN', new_user.email, fullname,
                    affected_data=[f"Creación de usuario {fullname} ({new_user.email})"])
        db.session.commit()

        logger.info("Usuario creado: %s (rol=%s) por %s", new_user.email, role.name, current_user_email())
        return api_success(data=new_user.to_dict(), mensaje="Usuario registrado correctamente.", http_status=201)

    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Error creating user")
        return api_error("ERROR_BASE_DATOS", "No se pudo crear el usuario. El username o email ya existe.", http_status=409)


###
#
# ✅ GET ALL USERS — Admin
#
###
@auth.route("/auth/user", methods=['GET'])
@require_role('Admin')
def user_get():
    page, size, err = _parse_page_size()
    if err:
        return err

    total = User.query.count()
    if not total:
        return api_error("USUARIOS_NO_ENCONTRADOS", "No se encontraron usuarios registrados.", http_status=404)

    init = (page - 1) * size
    users = db.session.execute(
        db.select(User).order_by(User.id.desc()).offset(init).limit(size)
    ).scalars().all()

    return api_success(
        data={"users": [u.to_dict() for u in users], **_pagination_meta(page, size, total, len(users))},
        mensaje="Usuarios obtenidos correctamente.",
    )


###
#
# ✅ GET USER BY ID — Admin o propio usuario
#
###
@auth.route("/auth/user/<int:user_id>", methods=['GET'])
@require_auth
def user_get_id(user_id):
    if not current_user_has_role('Admin') and current_user_id() != user_id:
        return api_error("ACCESO_DENEGADO", "Solo puedes consultar tu propio perfil.", http_status=403)

    user = db.session.get(User, user_id)
    if not user:
        return api_error("USUARIO_NO_ENCONTRADO", f"El usuario {user_id} no existe.", http_status=404)

    return api_success(data=user.to_dict(), mensaje="Usuario obtenido correctamente.")


###
#
# ✅ UPDATE USER — Admin o propio usuario
#
###
@auth.route("/auth/user/<int:user_id>", methods=['PUT'])
@require_auth
def user_update(user_id):
    if not current_user_has_role('Admin') and current_user_id() != user_id:
        return api_error("ACCESO_DENEGADO", "Solo puedes actualizar tu propio perfil.", http_status=403)

    user = db.session.get(User, user_id)
    if not user:
        return api_error("USUARIO_NO_ENCONTRADO", f"El usuario {user_id} no existe.", http_status=404)

    data = request.get_json()
    if not data:
        return api_error("CUERPO_REQUERIDO", "El cuerpo de la solicitud es requerido.")

    if 'email' in data and data['email']:
        if not current_user_has_role('Admin') and data['email'] != user.email:
            return api_error(
                "ACCESO_DENEGADO",
                "Solo un administrador puede modificar el email.",
                http_status=403, campo="email",
            )
        if not _validate_email(data['email']):
            return api_error("EMAIL_INVALIDO", "El formato del email no es válido.", campo="email")

    if data.get('password'):
        valid, msg = _validate_password(data['password'])
        if not valid:
            return api_error("PASSWORD_DEBIL", msg, campo="password")

    before = {
        'firstname': user.firstname,
        'lastname': user.lastname,
        'email': user.email,
        'phone': user.phone,
    }

    updatable = ('firstname', 'lastname', 'email', 'phone')
    for field in updatable:
        if field in data and data[field] is not None:
            setattr(user, field, data[field])

    after = {
        'firstname': user.firstname,
        'lastname': user.lastname,
        'email': user.email,
        'phone': user.phone,
    }
    descripcion = _describe_changes(before, after)

    if data.get('password'):
        user.set_password(data['password'])
        descripcion.append("Modificó la contraseña")

    user.modificated_at = datetime.now(timezone.utc)

    try:
        _audit_user(user.username, 'ACTUALIZACIÓN', user.email, f"{user.firstname} {user.lastname}",
                    affected_data=descripcion)
        db.session.commit()
        logger.info(
            "Usuario %s actualizado por %s: %s",
            user.email, current_user_email(), '; '.join(descripcion) or 'sin cambios',
        )
        return api_success(data=user.to_dict(), mensaje="Usuario actualizado correctamente.")
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Error updating user %d", user_id)
        return api_error("ERROR_BASE_DATOS", "No se pudo actualizar el usuario.", http_status=409)


###
#
# ✅ REMOVE USER — Admin
#
###
@auth.route("/auth/user/<int:user_id>", methods=['DELETE'])
@require_role('Admin')
def user_delete(user_id):
    user = db.session.get(User, user_id)
    if not user:
        return api_error("USUARIO_NO_ENCONTRADO", f"El usuario {user_id} no existe.", http_status=404)

    try:
        username = user.username
        email = user.email
        fullname = f"{user.firstname} {user.lastname}"
        db.session.delete(user)
        _audit_user(username, 'ELIMINACIÓN', email, fullname,
                    affected_data=[f"Eliminación de usuario {fullname} ({email})"])
        db.session.commit()
        logger.info("Usuario eliminado: %s por %s", email, current_user_email())
        return api_success(data={"id": user_id, "username": username}, mensaje="Usuario eliminado correctamente.")
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Error deleting user %d", user_id)
        return api_error("ERROR_BASE_DATOS", "No se pudo eliminar el usuario.", http_status=500)


###
#
# ✅ LIST ROLES — Admin
#
###
@auth.route("/auth/roles", methods=['GET'])
@require_role('Admin')
def role_list():
    roles = db.session.execute(db.select(Role).order_by(Role.id)).scalars().all()
    return api_success(data=[r.to_dict() for r in roles], mensaje="Roles obtenidos correctamente.")


###
#
# ✅ ASSIGN ROLE — Admin
#
###
@auth.route("/auth/user/<int:user_id>/role", methods=['POST'])
@require_role('Admin')
def user_assign_role(user_id):
    data = request.get_json()
    if not data or not data.get('role'):
        return api_error("CAMPO_REQUERIDO", "El campo 'role' es requerido.", campo="role")

    role_name = data.get('role', '').strip()
    if not role_name or len(role_name) > 100:
        return api_error("ROL_INVALIDO", "El campo 'role' es inválido.", campo="role")

    user = db.session.get(User, user_id)
    if not user:
        return api_error("USUARIO_NO_ENCONTRADO", f"El usuario {user_id} no existe.", http_status=404)

    role = Role.query.filter_by(name=role_name).first()
    if not role:
        return api_error("ROL_NO_ENCONTRADO", "El rol no existe.", http_status=404, campo="role")

    if user.has_role('Admin') and role_name != 'Admin':
        admin_count = User.query.filter(User.role.has(name='Admin')).count()
        if admin_count <= 1:
            return api_error(
                "OPERACION_NO_PERMITIDA",
                "No se puede degradar al último administrador del sistema.",
                http_status=409,
            )

    try:
        old_role = user.role.name if user.role else None
        user.set_role(role)
        descripcion = _describe_changes({'role': old_role}, {'role': role.name})
        _audit_user(user.username, 'ACTUALIZACIÓN', user.email, f"{user.firstname} {user.lastname}",
                    affected_data=descripcion)
        db.session.commit()
        logger.info(
            "Rol de %s cambiado de '%s' a '%s' por %s",
            user.email, old_role, role.name, current_user_email(),
        )
        return api_success(data=user.to_dict(), mensaje="Rol asignado correctamente.", http_status=201)
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Error assigning role to user %d", user_id)
        return api_error("ERROR_BASE_DATOS", "No se pudo asignar el rol.", http_status=500)


###
#
# ✅ REMOVE ROLE — Admin
#
###
@auth.route("/auth/user/<int:user_id>/role/<string:role_name>", methods=['DELETE'])
@require_role('Admin')
def user_remove_role(user_id, role_name):
    user = db.session.get(User, user_id)
    if not user:
        return api_error("USUARIO_NO_ENCONTRADO", f"El usuario {user_id} no existe.", http_status=404)

    role = Role.query.filter_by(name=role_name).first()
    if not role:
        return api_error("ROL_NO_ENCONTRADO", "El rol no existe.", http_status=404)

    if not user.has_role(role_name):
        return api_error("ROL_NO_ASIGNADO", "El usuario no tiene ese rol.", http_status=404)

    if role_name == 'Admin':
        admin_count = User.query.filter(User.role.has(name='Admin')).count()
        if admin_count <= 1:
            return api_error(
                "OPERACION_NO_PERMITIDA",
                "No se puede remover el último administrador del sistema.",
                http_status=409,
            )

    try:
        old_role = user.role.name if user.role else None
        user.set_role(None)
        descripcion = _describe_changes({'role': old_role}, {'role': None})
        _audit_user(user.username, 'ACTUALIZACIÓN', user.email, f"{user.firstname} {user.lastname}",
                    affected_data=descripcion)
        db.session.commit()
        logger.info("Rol '%s' removido de %s por %s", old_role, user.email, current_user_email())
        return api_success(data=user.to_dict(), mensaje="Rol eliminado correctamente.")
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Error removing role from user %d", user_id)
        return api_error("ERROR_BASE_DATOS", "No se pudo eliminar el rol.", http_status=500)


