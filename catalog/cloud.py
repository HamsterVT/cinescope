"""Server-side Supabase Auth/Data API boundary. No privileged key is used."""
import re

import httpx
from django.conf import settings
from django.core.signing import BadSignature
from django.urls import reverse

class CloudError(Exception):
    def __init__(self, status=503, code='unavailable'):
        self.status, self.code = status, code
        super().__init__(code)

def configured():
    return bool(settings.SUPABASE_URL and settings.SUPABASE_PUBLISHABLE_KEY)

def api(method, path, *, token=None, data=None, params=None, prefer=None, timeout=None):
    if not configured():
        raise CloudError(503, 'not_configured')
    headers = {'apikey': settings.SUPABASE_PUBLISHABLE_KEY}
    if token: headers['Authorization'] = 'Bearer ' + token
    if prefer: headers['Prefer'] = prefer
    try:
        r = httpx.request(method, settings.SUPABASE_URL + path, headers=headers,
                          json=data, params=params, timeout=timeout if timeout is not None else httpx.Timeout(10, connect=3))
        if not r.is_success:
            try: code = r.json().get('error_code') or r.json().get('code') or 'request_failed'
            except (ValueError, AttributeError): code = 'request_failed'
            raise CloudError(r.status_code, str(code))
        return r.json() if r.content else None
    except (httpx.HTTPError, ValueError) as exc:
        raise CloudError() from exc

def library(request):
    return api('GET', '/rest/v1/library', token=request.cloud_token,
               params={'select':'movie_id,saved,watched,rating,movie,updated_at',
                       'user_id':'eq.'+request.cloud_user['id'], 'order':'updated_at.desc','limit':'500'})

def set_tokens(response, tokens):
    # HttpOnly prevents JavaScript from accessing authentication credentials.
    for key, age in [('access_token', 3600), ('refresh_token', 60*60*24*30)]:
        response.set_cookie('cs_'+key, tokens[key], max_age=age,
                            httponly=True, secure=not settings.DEBUG, samesite='Lax', path='/')
    return response

def clear_tokens(response):
    for key in ['cs_access_token','cs_refresh_token','cs_pkce','cs_phone','cs_watch_return']:
        response.delete_cookie(key, path='/', samesite='Lax')
    return response

class CloudSessionMiddleware:
    def __init__(self, get_response): self.get_response = get_response
    def __call__(self, request):
        request.cloud_user = None
        request.cloud_token = request.COOKIES.get('cs_access_token', '')
        request.cloud_error = False
        refreshed = None
        invalid = False
        if configured() and (request.cloud_token or request.COOKIES.get('cs_refresh_token')):
            try:
                if not request.cloud_token: raise CloudError(401)
                request.cloud_user = api('GET','/auth/v1/user',token=request.cloud_token)
            except CloudError as exc:
                if exc.status in (400,401,403):
                    refresh = request.COOKIES.get('cs_refresh_token')
                    if refresh:
                        try:
                            refreshed = api('POST','/auth/v1/token',params={'grant_type':'refresh_token'},data={'refresh_token':refresh})
                            request.cloud_token = refreshed['access_token']
                            request.cloud_user = refreshed['user']
                        except CloudError as err:
                            invalid = err.status in (400,401,403)
                            request.cloud_error = not invalid
                    else: invalid = True
                else: request.cloud_error = True
        response = self.get_response(request)
        # Never replace tokens explicitly set by sign-in/sign-out with an old refresh.
        if refreshed and 'cs_access_token' not in response.cookies: set_tokens(response, refreshed)
        if invalid and 'cs_access_token' not in response.cookies: clear_tokens(response)
        # Return a newly signed-in buyer to the reviewed plan, never to an arbitrary URL.
        if response.status_code in (302,303) and response.get('Location') == '/profile/' and 'cs_access_token' in response.cookies:
            try:
                plan = request.get_signed_cookie('cs_checkout', salt='checkout-return', max_age=3600)
            except Exception:
                plan = None
            if plan in ('monthly','annual'):
                response['Location'] = '/billing/checkout/?plan=' + plan
                response.delete_cookie('cs_checkout', path='/', samesite='Lax')
            else:
                try:
                    movie_id = request.get_signed_cookie('cs_watch_return', salt='watch-return', max_age=1800)
                except (KeyError, BadSignature):
                    movie_id = ''
                if re.fullmatch(r'[a-z0-9][a-z0-9-]{0,99}', movie_id):
                    response['Location'] = reverse('watch', args=[movie_id])
                    response.delete_cookie('cs_watch_return', path='/', samesite='Lax')
        if request.COOKIES.get('cs_access_token') or request.COOKIES.get('cs_refresh_token') or request.path.startswith(('/account/','/profile/','/people/','/reviews/','/manage/','/api/v1/library/')):
            response['Cache-Control'] = 'private, no-store'
            response['Vary'] = 'Cookie'
        return response
