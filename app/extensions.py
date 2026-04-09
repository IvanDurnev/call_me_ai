from flask_mail import Mail
from flask_migrate import Migrate
from flask_sock import Sock
from flask_sqlalchemy import SQLAlchemy


db = SQLAlchemy()
migrate = Migrate()
sock = Sock()
mail = Mail()
