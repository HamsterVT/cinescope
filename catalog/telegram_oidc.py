"""RS256 public-key compatibility for Auth versions unable to parse mixed JWKS.

Only Telegram's public RSA keys are relayed. Auth still verifies the original
Telegram token, issuer and audience; authorization and token exchange stay at
Telegram. This endpoint never accepts tokens or supplies signing keys.
"""
import base64
import re
import httpx
from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.views.decorators.http import require_GET

ISSUER = 'https://oauth.telegram.org'
KEYS_URL = ISSUER + '/.well-known/jwks.json'


@require_GET
def discovery(request):
    return JsonResponse({
        'issuer': ISSUER, 'authorization_endpoint': ISSUER + '/auth',
        'token_endpoint': ISSUER + '/token',
        'jwks_uri': settings.SITE_URL.rstrip('/') + '/account/telegram/oidc/keys/',
        'response_types_supported': ['code'], 'subject_types_supported': ['public'],
        'id_token_signing_alg_values_supported': ['RS256'],
        'token_endpoint_auth_methods_supported': ['client_secret_basic', 'client_secret_post'],
        'scopes_supported': ['openid', 'profile'],
        'code_challenge_methods_supported': ['S256'],
        'grant_types_supported': ['authorization_code'],
    })


def public_rsa_keys(document):
    result = []
    for key in document.get('keys', []):
        if not isinstance(key, dict) or key.get('kty') != 'RSA' or key.get('alg') != 'RS256':
            continue
        if key.get('use', 'sig') != 'sig' or 'verify' not in key.get('key_ops', ['verify']):
            continue
        if not isinstance(key.get('kid'), str) or not 1 <= len(key['kid']) <= 200:
            continue
        if not all(isinstance(key.get(k), str) and re.fullmatch(r'[A-Za-z0-9_-]{1,12000}', key[k]) for k in ('n', 'e')):
            continue
        modulus = int.from_bytes(base64.urlsafe_b64decode(key['n'] + '=' * (-len(key['n']) % 4)), 'big')
        if modulus.bit_length() < 2048:
            continue
        result.append({k: key[k] for k in ('kty', 'alg', 'kid', 'n', 'e')})
    if not result:
        raise ValueError('No supported Telegram public keys')
    return {'keys': result}


@require_GET
def keys(request):
    document = cache.get('telegram-rs256-public-jwks-v1')
    if document is None:
        try:
            response = httpx.get(KEYS_URL, timeout=httpx.Timeout(8, connect=3), follow_redirects=False)
            response.raise_for_status()
            if len(response.content) > 1000000:
                raise ValueError('Invalid key response')
            document = public_rsa_keys(response.json())
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            return JsonResponse({'error': 'telegram_keys_unavailable'}, status=503)
        cache.set('telegram-rs256-public-jwks-v1', document, 60)
    return JsonResponse(document)
