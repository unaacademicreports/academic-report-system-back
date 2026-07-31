from flask import Blueprint

reports_bp = Blueprint('report', __name__, template_folder='templates')

from app.reports import routes