import os

class Config:
    SECRET_KEY = os.environ.get('SCHEDULE_SECRET_KEY') or 'dev_key_change_in_production'
    
    # Legacy compatibility: some older deployments used postgres:// URLs,
    # but SQLAlchemy expects postgresql://.
    basedir = os.path.abspath(os.path.dirname(__file__))
    _db_url = os.environ.get('DATABASE_URL')
    if _db_url and _db_url.startswith('postgres://'):
        _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
    SQLALCHEMY_DATABASE_URI = _db_url or 'sqlite:///' + os.path.join(basedir, 'schedule.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Session security
    # Only enable Secure cookies when explicitly set AND using HTTPS.
    # Plain HTTP (for example a direct Oracle host/IP without SSL) will silently drop
    # Secure cookies, breaking login entirely.
    SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', 'False').lower() == 'true'
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 90  # 90 days
    
    # Use HTTPS for generated links only when Secure cookies are enabled
    PREFERRED_URL_SCHEME = 'https' if os.environ.get('SESSION_COOKIE_SECURE', 'False').lower() == 'true' else 'http'
    
    # Email settings
    EMAIL_ADDRESS = os.environ.get('SCHEDULE_EMAIL', '')
    EMAIL_PASSWORD = os.environ.get('SCHEDULE_EMAIL_APP_PASSWORD', '')
    
    # Telegram settings
    TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')
    TELEGRAM_PERSONAL_CHAT_ID = os.environ.get('TELEGRAM_PERSONAL_CHAT_ID', '27859948')
    TELEGRAM_WEBHOOK_SECRET = os.environ.get('TELEGRAM_WEBHOOK_SECRET', '')

    # Notification settings
    REMINDER_HOUR = int(os.environ.get('REMINDER_HOUR', '9'))  # 9 AM Vancouver time

    # External URL for generating links (for example Telegram pickup links).
    # In live environments prefer the Oracle public domain via env var.
    BASE_URL = os.environ.get('BASE_URL', 'https://livestream.disterhoft.com')


class DevelopmentConfig(Config):
    DEBUG = True
    SESSION_COOKIE_SECURE = False


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
