from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_sqlalchemy import SQLAlchemy


db = SQLAlchemy()
cors = CORS()
# Storage en memoria: el límite es por worker de gunicorn, suficiente como
# primera capa. Para límite global compartido usar storage_uri de Redis.
limiter = Limiter(key_func=get_remote_address, storage_uri="memory://")
