from flask import Blueprint

exports_bp = Blueprint('exports', __name__, template_folder='templates')

from app.exports import routes