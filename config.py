import os

class Config:
    SECRET_KEY = os.environ.get('SCHEDULE_SECRET_KEY') or 'dev_key_change_in_production'
    
    # Database
    basedir = os.path.abspath(os.path.dirname(__file__))
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'sqlite:///' + os.path.join(basedir, 'schedule.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Session security
    SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', 'False').lower() == 'true'
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 90  # 90 days
    
    # Email settings
    EMAIL_ADDRESS = os.environ.get('SCHEDULE_EMAIL', '')
    EMAIL_PASSWORD = os.environ.get('SCHEDULE_EMAIL_APP_PASSWORD', '')
    
    # Telegram settings
    TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')
    
    # Notification settings
    REMINDER_HOUR = int(os.environ.get('REMINDER_HOUR', '8'))  # 8 AM


class DevelopmentConfig(Config):
    DEBUG = True
    SESSION_COOKIE_SECURE = False


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
