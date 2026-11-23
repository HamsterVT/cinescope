from urllib.parse import urlsplit
from django.conf import settings


class HeadersMiddleware:
    def __init__(self, get_response): self.get_response = get_response
    def __call__(self, request):
        response = self.get_response(request)
        form_action = "'self'"
        match = getattr(request, 'resolver_match', None)
        if getattr(match, 'url_name', None) in ('billing_checkout','billing_order'):
            from .payments import secure_origin, PaymentError
            try: form_action += ' ' + secure_origin(settings.BTCPAY_URL)
            except PaymentError: pass
        if settings.AUTH_TELEGRAM_READY and getattr(match, 'url_name', None) in (
                'login', 'signup', 'profile', 'public_profile', 'telegram_start'):
            # Chromium also checks form-action on redirects after the local POST.
            # Scope the OAuth destinations to pages that initiate this flow.
            try:
                auth = urlsplit(settings.SUPABASE_URL)
                if (auth.scheme == 'https' and auth.hostname and not auth.username
                        and not auth.password and auth.port in (None, 443)):
                    form_action += f" https://{auth.hostname} https://oauth.telegram.org"
            except ValueError:
                pass
        response["Content-Security-Policy"] = "default-src 'self'; img-src 'self' https://image.tmdb.org data:; style-src 'self' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; script-src 'self' https://www.youtube.com https://s.ytimg.com; connect-src 'self'; frame-src https://www.youtube-nocookie.com; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action " + form_action
        response["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if getattr(request, 'video_origin', ''):
            response['Content-Security-Policy'] += "; media-src 'self' " + request.video_origin
        return response
