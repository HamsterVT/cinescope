import base64
import copy
import time
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from django.core import signing
from django.test import Client, SimpleTestCase, override_settings
from .test_accounts import USER, TOKENS, STORES
from .test_social import FakeSocial, OTHER
from .cloud import CloudError
from .telegram import linked, safe_provider_url
from django.core.cache import cache
from .telegram_oidc import public_rsa_keys, KEYS_URL


@override_settings(ALLOWED_HOSTS=['testserver'], STORAGES=STORES,
                   SITE_URL='https://cinescope-beta-five.vercel.app')
class TelegramOidcKeysTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        n = base64.urlsafe_b64encode((2**2047 + 65537).to_bytes(256, 'big')).decode().rstrip('=')
        self.rsa = {'kty': 'RSA', 'alg': 'RS256', 'kid': 'rotating-key', 'n': n, 'e': 'AQAB'}

    def test_discovery_keeps_telegram_issuer_and_token_exchange(self):
        result = self.client.get('/account/telegram/oidc/discovery/', HTTP_HOST='testserver').json()
        self.assertEqual(result['issuer'], 'https://oauth.telegram.org')
        self.assertEqual(result['token_endpoint'], 'https://oauth.telegram.org/token')
        self.assertEqual(result['id_token_signing_alg_values_supported'], ['RS256'])
        self.assertEqual(result['jwks_uri'], 'https://cinescope-beta-five.vercel.app/account/telegram/oidc/keys/')

    def test_mixed_keys_are_filtered_without_changing_public_rsa_material(self):
        result = public_rsa_keys({'keys': [{**self.rsa, 'd': 'never-relay-private-material'},
            {'kty': 'EC', 'alg': 'ES256K', 'crv': 'secp256k1'}, {**self.rsa, 'alg': 'none'}]})
        self.assertEqual(result, {'keys': [self.rsa]})
        with self.assertRaises(ValueError): public_rsa_keys({'keys': [{**self.rsa, 'n': 'AQAB'}]})

    @patch('catalog.telegram_oidc.httpx.get')
    def test_fixed_upstream_and_short_cache_support_key_rotation(self, get):
        get.return_value.content = b'{}'
        get.return_value.json.return_value = {'keys': [self.rsa]}
        for _ in range(2):
            self.assertEqual(self.client.get('/account/telegram/oidc/keys/?url=https://evil.test').json(), {'keys': [self.rsa]})
        self.assertEqual(get.call_count, 1)
        self.assertEqual(get.call_args.args, (KEYS_URL,))
        self.assertFalse(get.call_args.kwargs['follow_redirects'])
        cache.clear()
        get.return_value.json.return_value = {'keys': [{**self.rsa, 'kid': 'next-key'}]}
        self.assertEqual(self.client.get('/account/telegram/oidc/keys/').json()['keys'][0]['kid'], 'next-key')

    @patch('catalog.telegram_oidc.httpx.get')
    def test_bad_upstream_fails_closed_and_does_not_cache_failures(self, get):
        get.return_value.content = b'{}'
        get.return_value.json.return_value = {'keys': [{'kty': 'EC', 'alg': 'ES256K'}]}
        for _ in range(2): self.assertEqual(self.client.get('/account/telegram/oidc/keys/').status_code, 503)
        self.assertEqual(get.call_count, 2)

TG_USER={**USER,'identities':[{'provider':'custom:telegram','identity_id':'telegram-identity'}]}

@override_settings(ALLOWED_HOSTS=['testserver'],STORAGES=STORES,
    SUPABASE_URL='https://test.supabase.co',SUPABASE_PUBLISHABLE_KEY='test-key',
    SITE_URL='https://cinescope-beta-five.vercel.app',AUTH_TELEGRAM_READY=True)
class TelegramTests(SimpleTestCase):
    def setUp(self):
        self.fake=FakeSocial()
        self.result_user=copy.deepcopy(TG_USER)
        self.link_url='https://oauth.telegram.org/auth?state=signed-by-supabase'
        self.calls=[]
        self.api=patch('catalog.cloud.api',side_effect=self.api_request).start()
        self.addCleanup(patch.stopall)

    def api_request(self,method,path,**kw):
        self.calls.append((method,path,kw))
        if path=='/auth/v1/token':return {**TOKENS,'user':self.result_user}
        if path=='/auth/v1/user/identities/authorize':return {'url':self.link_url}
        if path=='/auth/v1/user' and kw.get('token')=='test-access' and not self.client.cookies.get('cs_access_token'):
            return self.result_user
        # During linking the new session and current session use a distinct token.
        if path=='/auth/v1/user' and kw.get('token')=='new-access':return self.result_user
        return self.fake(method,path,**kw)

    def state(self,mode='telegram_login',uid=None):
        state={'v':'random-verifier','mode':mode}
        if uid:state['uid']=uid
        self.client.cookies['cs_pkce']=signing.dumps(state,salt='pkce')

    def test_start_uses_pkce_and_fixed_provider_redirect(self):
        r=self.client.post('/account/telegram/',{'next':'https://evil.test','provider':'evil'})
        self.assertEqual(r.status_code,302)
        url=urlsplit(r['Location']);params=parse_qs(url.query)
        self.assertEqual(url.netloc,'test.supabase.co')
        self.assertEqual(params['provider'],['custom:telegram'])
        self.assertEqual(params['redirect_to'],['https://cinescope-beta-five.vercel.app/account/callback/'])
        self.assertEqual(params['code_challenge_method'],['s256'])
        cookie=r.cookies['cs_pkce'];self.assertTrue(cookie['httponly']);self.assertTrue(cookie['secure'])
        state=signing.loads(cookie.value,salt='pkce')
        self.assertNotEqual(state['v'],params['code_challenge'][0])
        self.assertNotIn('test-access',r['Location'])
        self.assertIn('no-store',r['Cache-Control'])

    def test_csrf_and_post_required(self):
        self.assertEqual(self.client.get('/account/telegram/').status_code,405)
        self.assertEqual(Client(enforce_csrf_checks=True).post('/account/telegram/').status_code,403)

    def test_provider_verification_error_is_not_reported_as_user_cancellation(self):
        self.state()
        response = self.client.get('/account/callback/?error=server_error&error_code=unexpected_failure&error_description=private-upstream-detail')
        self.assertContains(response, 'Сервис авторизации не смог проверить вход', status_code=503)
        self.assertNotIn('отменено', response.content.decode())
        self.assertNotIn('private-upstream-detail', response.content.decode())

    @override_settings(AUTH_TELEGRAM_READY=False)
    def test_unconfigured_does_not_offer_or_start_a_broken_login(self):
        self.assertNotContains(self.client.get('/account/login/'),'Продолжить с Telegram')
        self.assertEqual(self.client.post('/account/telegram/').status_code,503)
        self.assertEqual(self.calls,[])

    def test_telegram_button_is_available_for_login_and_signup(self):
        for path in ['/account/login/','/account/signup/']:
            r=self.client.get(path)
            self.assertContains(r,'Продолжить с Telegram')
            self.assertContains(r,'привяжите Telegram')
            self.assertContains(r,'id="email-form"')
            self.assertNotContains(r,'Телефон<small>Скоро')
        self.assertNotContains(self.client.get('/account/recover/'),'Продолжить с Telegram')

    def test_csp_permits_the_required_oauth_redirect_origins_on_auth_pages(self):
        expected = "form-action 'self' https://test.supabase.co https://oauth.telegram.org"
        for path in ['/account/login/', '/account/signup/', '/profile/']:
            policy = self.client.get(path)['Content-Security-Policy']
            self.assertEqual(policy.split('; ')[-1], expected)
            self.assertIn("object-src 'none'; base-uri 'self'; frame-ancestors 'none'", policy)
        started = self.client.post('/account/telegram/')
        self.assertEqual(started['Content-Security-Policy'].split('; ')[-1], expected)

    def test_csp_keeps_other_pages_and_disabled_auth_local(self):
        for path in ['/catalog/', '/account/recover/']:
            self.assertEqual(self.client.get(path)['Content-Security-Policy'].split('; ')[-1], "form-action 'self'")
        with override_settings(AUTH_TELEGRAM_READY=False):
            self.assertEqual(self.client.get('/account/login/')['Content-Security-Policy'].split('; ')[-1], "form-action 'self'")

    def test_successful_callback_checks_actual_identity_and_sets_session(self):
        self.state()
        r=self.client.get('/account/callback/?code=single-use&next=https://evil.test')
        self.assertEqual(r.status_code,302);self.assertEqual(r['Location'],'/profile/')
        self.assertTrue(r.cookies['cs_access_token']['httponly'])
        self.assertEqual(r.cookies['cs_pkce']['max-age'],0)
        self.assertTrue(any(c[1]=='/auth/v1/user' for c in self.calls))
        self.assertFalse(any(c[1].startswith('/rest/v1/') for c in self.calls))

    def test_missing_expired_and_forged_state_cannot_exchange_a_code(self):
        for value in ['',signing.dumps({'v':'x','mode':'telegram_login'},salt='wrong')]:
            self.client.cookies['cs_pkce']=value
            self.assertEqual(self.client.get('/account/callback/?code=stolen').status_code,400)
        self.state()
        with patch('django.core.signing.time.time',return_value=time.time()+601):
            self.assertEqual(self.client.get('/account/callback/?code=expired').status_code,400)
        self.assertEqual(self.calls,[])

    def test_provider_error_is_safe_and_does_not_exchange(self):
        self.state()
        r=self.client.get('/account/callback/?error=access_denied&error_description=secret-value')
        self.assertContains(r,'Подтверждение Telegram отменено',status_code=400)
        self.assertNotContains(r,'secret-value',status_code=400)
        self.assertEqual(self.calls,[])

    def test_metadata_cannot_impersonate_a_verified_identity(self):
        self.result_user={**USER,'user_metadata':{'provider':'custom:telegram','role':'admin'}}
        self.assertFalse(linked(self.result_user));self.state()
        r=self.client.get('/account/callback/?code=valid')
        self.assertEqual(r.status_code,400)
        self.assertNotIn('cs_access_token',r.cookies)

    def test_link_requires_login_and_uses_current_user_token(self):
        r=self.client.post('/account/telegram/',{'intent':'link','user_id':OTHER})
        self.assertEqual(r['Location'],'/account/login/')
        self.client.cookies['cs_access_token']='existing-access'
        r=self.client.post('/account/telegram/',{'intent':'link','user_id':OTHER})
        self.assertEqual(r['Location'],self.link_url)
        state=signing.loads(r.cookies['cs_pkce'].value,salt='pkce')
        self.assertEqual(state['uid'],USER['id'])
        call=next(c for c in self.calls if c[1]=='/auth/v1/user/identities/authorize')
        self.assertEqual(call[2]['token'],'existing-access')
        self.assertEqual(call[2]['params']['skip_http_redirect'],'true')

    def test_linked_callback_preserves_user_id_and_profile_data(self):
        self.client.cookies['cs_access_token']='existing-access'
        self.state('telegram_link',USER['id'])
        original=copy.deepcopy(self.fake.tables)
        def response(method,path,**kw):
            if path=='/auth/v1/token':return {**TOKENS,'access_token':'new-access','user':TG_USER}
            return self.api_request(method,path,**kw)
        self.api.side_effect=response
        r=self.client.get('/account/callback/?code=link')
        self.assertEqual(r.status_code,302)
        self.assertEqual(r['Location'],'/profile/?tab=settings&telegram=linked')
        self.assertEqual(r.cookies['cs_access_token'].value,'new-access')
        self.assertEqual(self.fake.tables,original)

    def test_callback_refuses_different_user_in_link_result(self):
        self.client.cookies['cs_access_token']='existing-access'
        self.state('telegram_link',USER['id'])
        self.result_user={**TG_USER,'id':OTHER}
        def response(method,path,**kw):
            if path=='/auth/v1/token':return {**TOKENS,'access_token':'new-access'}
            return self.api_request(method,path,**kw)
        self.api.side_effect=response
        r=self.client.get('/account/callback/?code=wrong-user')
        self.assertEqual(r.status_code,400)
        self.assertNotIn('cs_access_token',r.cookies)

    def test_link_callback_requires_original_live_session(self):
        self.state('telegram_link',USER['id'])
        r=self.client.get('/account/callback/?code=valid')
        self.assertEqual(r.status_code,400)
        self.assertEqual(self.calls,[])

    def test_only_known_https_telegram_authorization_url_is_accepted(self):
        for value in ['https://evil.test/auth','http://oauth.telegram.org/auth',
                      'https://oauth.telegram.org.evil.test/auth','https://oauth.telegram.org:444/auth',
                      'https://user@oauth.telegram.org/auth','javascript:alert(1)',None]:
            self.assertFalse(safe_provider_url(value),value)
        self.client.cookies['cs_access_token']='existing-access'
        self.link_url='https://evil.test/auth'
        r=self.client.post('/account/telegram/',{'intent':'link'})
        self.assertEqual(r.status_code,503);self.assertNotIn('Location',r)

    def test_profile_shows_identity_and_no_unusable_password_form(self):
        self.client.cookies['cs_access_token']='existing-access'
        r=self.client.get('/profile/?tab=settings')
        self.assertContains(r,'Привязать Telegram')
        self.fake.user.update(email='',phone='',identities=TG_USER['identities'])
        r=self.client.get('/profile/?tab=settings')
        self.assertContains(r,'Telegram привязан')
        self.assertNotContains(r,'id="new-password"')
        self.assertNotContains(r,'Привязать Telegram')

    def test_link_outage_does_not_modify_session(self):
        self.client.cookies['cs_access_token']='existing-access'
        def response(method,path,**kw):
            if path=='/auth/v1/user/identities/authorize':raise CloudError()
            return self.api_request(method,path,**kw)
        self.api.side_effect=response
        r=self.client.post('/account/telegram/',{'intent':'link'})
        self.assertEqual(r.status_code,503)
        self.assertNotIn('cs_access_token',r.cookies)
