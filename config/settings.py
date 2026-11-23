import os
from pathlib import Path
import dj_database_url
from django.core.exceptions import ImproperlyConfigured
BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.getenv("DJANGO_DEBUG", "0") == "1"
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if os.getenv("VERCEL_ENV") == "production":
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be configured")
    SECRET_KEY = "local-development-only-key-never-use-for-production-cinescope"
ALLOWED_HOSTS = [x.strip() for x in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,.vercel.app").split(",") if x.strip()]
INSTALLED_APPS = ["django.contrib.contenttypes", "django.contrib.staticfiles", "catalog"]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware", "whitenoise.middleware.WhiteNoiseMiddleware", "django.middleware.common.CommonMiddleware", "django.middleware.csrf.CsrfViewMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware", "catalog.middleware.HeadersMiddleware", "catalog.cloud.CloudSessionMiddleware"]
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
TEMPLATES = [{"BACKEND":"django.template.backends.django.DjangoTemplates","DIRS":[BASE_DIR / "templates"],"APP_DIRS":True,"OPTIONS":{"context_processors":["django.template.context_processors.request","catalog.context.site"]}}]
DATABASES = {"default": dj_database_url.config(default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}", conn_max_age=0, conn_health_checks=True)}
LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "Asia/Almaty"
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {"default":{"BACKEND":"django.core.files.storage.FileSystemStorage"},"staticfiles":{"BACKEND":"whitenoise.storage.CompressedManifestStaticFilesStorage"}}
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = os.getenv("VERCEL_ENV") == "production"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_HSTS_SECONDS = 31536000 if os.getenv("VERCEL_ENV") == "production" else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"
MOVIE_PROVIDER = os.getenv("MOVIE_PROVIDER", "local")
TMDB_READ_TOKEN = os.getenv("TMDB_READ_TOKEN", "")
CACHES = {"default":{"BACKEND":"django.core.cache.backends.locmem.LocMemCache","LOCATION":"cinescope","TIMEOUT":300,"OPTIONS":{"MAX_ENTRIES":500}}}
LOGGING = {"version":1,"disable_existing_loggers":False,"handlers":{"console":{"class":"logging.StreamHandler"}},"root":{"handlers":["console"],"level":"WARNING"}}

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_PUBLISHABLE_KEY = os.getenv("SUPABASE_PUBLISHABLE_KEY", "")
SITE_URL = os.getenv("SITE_URL", "https://cinescope-beta-five.vercel.app")

AUTH_EMAIL_READY = os.getenv("AUTH_EMAIL_READY", "0") == "1"
# Enable only after the Supabase Twilio WhatsApp sender has been verified end-to-end.
AUTH_PHONE_READY = os.getenv("AUTH_PHONE_READY", "0") == "1"
# Enable only after the custom:telegram OIDC provider and manual linking are verified.
AUTH_TELEGRAM_READY = os.getenv("AUTH_TELEGRAM_READY", "0") == "1"

# Server-side BTCPay credentials for Plus checkout and webhook verification.
BTCPAY_URL = os.getenv("BTCPAY_URL", "")
BTCPAY_STORE_ID = os.getenv("BTCPAY_STORE_ID", "")
BTCPAY_API_KEY = os.getenv("BTCPAY_API_KEY", "")
BTCPAY_WEBHOOK_SECRET = os.getenv("BTCPAY_WEBHOOK_SECRET", "")
BTCPAY_WEBHOOK_ID = os.getenv("BTCPAY_WEBHOOK_ID", "")
SUPABASE_BILLING_SECRET_KEY = os.getenv("SUPABASE_BILLING_SECRET_KEY", "")
VIDEO_PLAYBACK_ENABLED = os.getenv("VIDEO_PLAYBACK_ENABLED", "1") == "1"
VIDEO_MEDIA_HOSTS = tuple(host.strip().lower() for host in os.getenv("VIDEO_MEDIA_HOSTS", "").split(",") if host.strip())

# Short-lived screening link, available only to the named CineScope account(s).
FEATURED_DEMO_MP4 = os.getenv("CINESCOPE_DEMO_MP4", "").strip()
FEATURED_DEMO_USERS = frozenset(user.strip() for user in os.getenv("CINESCOPE_DEMO_USERS", "").split(",") if user.strip())
