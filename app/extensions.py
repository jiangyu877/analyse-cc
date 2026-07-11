from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect


db = SQLAlchemy(session_options={"expire_on_commit": False})
csrf = CSRFProtect()

