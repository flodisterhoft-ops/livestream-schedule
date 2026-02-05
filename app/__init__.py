from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix
from .extensions import db
from .routes import bp as main_bp

def create_app(config_class='config.Config'):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Fix for Render (handle reverse proxy headers for HTTPS)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    db.init_app(app)

    app.register_blueprint(main_bp)

    with app.app_context():
        # Create tables if they don't exist
        db.create_all()
        
        # Seed database with schedule data if empty
        from .seed_data import seed_database
        seed_database()

    return app
