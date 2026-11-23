"""Telegram OIDC through Supabase Auth. No bot secret or privileged key in Django."""
from urllib.parse import urlencode, urlsplit
from django.conf import settings
from django.core import signing
from django.middleware.csrf import rotate_token
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST
from . import cloud
from .accounts import pkce

PROVIDER = 'custom:telegram'
MODES = ('telegram_login', 'telegram_link')

def ready():
    return settings.AUTH_TELEGRAM_READY and cloud.configured()

def linked(user):
    # Identities come from GET /auth/v1/user, never from editable user_metadata.
    return bool(user and any(isinstance(i, dict) and i.get('provider') == PROVIDER
                             for i in (user.get('identities') or [])))

def failure(request, text, status=400, *, clear_pending=True):
    response = render(request, 'error.html', {'message': text}, status=status)
    if clear_pending:
        response.delete_cookie('cs_pkce', path='/', samesite='Lax')
    return response

def safe_provider_url(value):
    if not isinstance(value, str) or len(value) > 10000:
        return False
    try:
        url = urlsplit(value)
        return (url.scheme == 'https' and url.hostname == 'oauth.telegram.org'
                and url.port in (None, 443) and not url.username and not url.password
                and url.path in ('/auth', '/auth/'))
    except ValueError:
        return False

@require_POST
def start(request):
    intent = request.POST.get('intent', 'login')
    if intent not in ('login', 'link'):
        return failure(request, 'Неизвестный способ входа.')
    if intent == 'link' and not request.cloud_user:
        return redirect('/account/login/')
    if intent == 'login' and request.cloud_user:
        return redirect('/profile/')
    if not ready():
        return failure(request, 'Вход через Telegram ещё подключается. Пока используйте email.', 503)
    if intent == 'link' and linked(request.cloud_user):
        return redirect('/profile/?tab=settings')
    verifier, challenge = pkce()
    params = {'provider': PROVIDER, 'redirect_to': settings.SITE_URL + '/account/callback/',
              'code_challenge': challenge, 'code_challenge_method': 's256'}
    state = {'v': verifier, 'mode': 'telegram_' + intent}
    if intent == 'link':
        state['uid'] = request.cloud_user['id']
        try:
            result = cloud.api('GET', '/auth/v1/user/identities/authorize', token=request.cloud_token,
                               params={**params, 'skip_http_redirect': 'true'})
        except cloud.CloudError:
            return failure(request, 'Не удалось начать привязку Telegram. Ваш профиль не изменён. Попробуйте позже.', 503)
        url = result.get('url') if isinstance(result, dict) else None
        if not safe_provider_url(url):
            return failure(request, 'Сервис вернул некорректный адрес входа. Попробуйте позже.', 503)
    else:
        url = settings.SUPABASE_URL + '/auth/v1/authorize?' + urlencode(params)
    response = redirect(url)
    response.set_cookie('cs_pkce', signing.dumps(state, salt='pkce'), max_age=600,
                        httponly=True, secure=not settings.DEBUG, samesite='Lax', path='/')
    return response

def finish(request):
    """Called only by the shared, allow-listed Supabase callback."""
    try:
        state = signing.loads(request.COOKIES.get('cs_pkce', ''), salt='pkce', max_age=600)
        if not ready() or state.get('mode') not in MODES:
            raise ValueError()
        is_link = state['mode'] == 'telegram_link'
        if is_link and (not request.cloud_user or request.cloud_user['id'] != state.get('uid')):
            return failure(request, 'Сеанс изменился. Войдите в нужный профиль и повторите привязку Telegram.')
        if request.GET.get('error'):
            if request.GET.get('error_code') in ('identity_already_exists', 'identity_already_linked'):
                return failure(request, 'Этот Telegram уже связан с другим профилем CineScope. Войдите через него или используйте email исходного профиля.')
            if request.GET.get('error') == 'access_denied' and request.GET.get('error_code', '') in ('', 'access_denied'):
                return failure(request, 'Подтверждение Telegram отменено. Можно повторить вход или использовать email.')
            return failure(request, 'Сервис авторизации не смог проверить вход через Telegram. Попробуйте ещё раз или используйте email.', 503)
        code = request.GET.get('code', '')
        if not code or len(code) > 2048:
            raise ValueError()
        result = cloud.api('POST', '/auth/v1/token', params={'grant_type': 'pkce'},
                           data={'auth_code': code, 'code_verifier': state['v']})
        if not isinstance(result, dict) or not result.get('access_token') or not result.get('refresh_token'):
            raise ValueError()
        # Check the actual authenticated identity again before accepting any session.
        user = cloud.api('GET', '/auth/v1/user', token=result['access_token'])
        if not isinstance(user, dict) or not user.get('id') or not linked(user):
            raise ValueError()
        if is_link and user['id'] != state['uid']:
            return failure(request, 'Telegram не привязан к исходному профилю. Текущий аккаунт сохранён; повторите привязку в настройках.')
        if not is_link and request.cloud_user and request.cloud_user['id'] != user['id']:
            return failure(request, 'Вы уже вошли в другой профиль. Сначала выйдите из него и повторите вход через Telegram.')
        rotate_token(request)
        response = cloud.set_tokens(redirect('/profile/?tab=settings&telegram=linked' if is_link else '/profile/'), result)
        response.delete_cookie('cs_pkce', path='/', samesite='Lax')
        return response
    except (signing.BadSignature, ValueError, KeyError, TypeError):
        return failure(request, 'Ссылка входа устарела или открыта в другом браузере. Начните вход через Telegram заново.')
    except cloud.CloudError:
        return failure(request, 'Не удалось завершить вход через Telegram. Попробуйте ещё раз или используйте email.', 503)
