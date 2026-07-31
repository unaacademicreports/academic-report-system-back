from .models.role import Role
from .models.user import User
from .models.revoked_token import RevokedToken

from app.extensions import db

from flask import Blueprint
auth_bp = Blueprint('auth', __name__, template_folder="templates")

from flask_login import LoginManager
login_manager = LoginManager()

from . import routes


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


@auth_bp.cli.command("init-roles")
def init_roles():
    """Create the different roles for the auth module."""
    roles = [
        {"name": "Admin", "description": "Can create, update, and view auth module objects"},
        {"name": "Editor", "description": "Can update and view auth module objects"},
        {"name": "Viewer", "description": "Can only view auth module objects"}
    ]
    for r in roles:
        role = Role.query.filter_by(name=r['name']).first()
        if not role:
            role = Role(name=r['name'], description=r['description'])
            db.session.add(role)
    db.session.commit()
    print("Roles created successfully: Admin, Editor, Viewer")

##
#
#   ✅ To import auth to any project
#
#   from app.auth import auth_bp
#   app.register_blueprint(auth_bp)
#
##
