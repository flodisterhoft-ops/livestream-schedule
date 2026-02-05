import os

class Config:
    SECRET_KEY = os.environ.get('SCHEDULE_SECRET_KEY') or 'dev_key_change_in_production'
    
    # Database - fix for Render's postgres:// URL (SQLAlchemy requires postgresql://)
    basedir = os.path.abspath(os.path.dirname(__file__))
    _db_url = os.environ.get('DATABASE_URL')
    if _db_url and _db_url.startswith('postgres://'):
        _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
    SQLALCHEMY_DATABASE_URI = _db_url or 'sqlite:///' + os.path.join(basedir, 'schedule.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Session security
    SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', 'False').lower() == 'true'
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 90  # 90 days
    
    # Ensure generated links use HTTPS
    PREFERRED_URL_SCHEME = 'https'
    
    # Email settings
    EMAIL_ADDRESS = os.environ.get('SCHEDULE_EMAIL', '')
    EMAIL_PASSWORD = os.environ.get('SCHEDULE_EMAIL_APP_PASSWORD', '')
    
    # Telegram settings
    TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')
    
    # Notification settings
    REMINDER_HOUR = int(os.environ.get('REMINDER_HOUR', '8'))  # 8 AM
    
    # External URL for generating links (e.g., Telegram pickup links)
    BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5000')


class DevelopmentConfig(Config):
    DEBUG = True
    SESSION_COOKIE_SECURE = False


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
