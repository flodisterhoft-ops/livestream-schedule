from flask import Flask
from .extensions import db
from .routes import bp as main_bp

def create_app(config_class='config.Config'):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)

    app.register_blueprint(main_bp)

    with app.app_context():
        # Create tables if they don't exist
        db.create_all()

    return app
