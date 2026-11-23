import json
from unittest.mock import patch
from django.test import SimpleTestCase, override_settings, Client
from .cloud import CloudError

USER={'id':'4fba1f17-5baf-4211-90d4-ab3e79d619d8','email':'test@example.test','user_metadata':{'display_name':'Тест'}}
TOKENS={'access_token':'test-access','refresh_token':'test-refresh','user':USER}
STORES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}}
@override_settings(ALLOWED_HOSTS=['testserver'],STORAGES=STORES,SUPABASE_URL='https://test.supabase.co',SUPABASE_PUBLISHABLE_KEY='test-key',AUTH_EMAIL_READY=True)
class AccountsTests(SimpleTestCase):
    def login_client(self): self.client.cookies['cs_access_token']='test-access'
    def test_guest_and_new_pages(self):
        for url in ['/profile/','/account/login/','/account/signup/','/account/recover/','/subscription/','/series/','/top/']:
            self.assertEqual(self.client.get(url).status_code,200,url)
        self.assertEqual(self.client.get('/api/v1/library/').status_code,401)
        self.assertEqual(self.client.get('/account/logout/').status_code,405)
    def test_real_type_and_rating_filters(self):
        data=self.client.get('/api/v1/movies/',{'type':'tv','rating':'9'}).json()
        self.assertGreater(data['count'],0)
        self.assertTrue(all(m['media_type']=='tv' and m['rating']>=9 for m in data['results']))
        r=self.client.get('/series/'); self.assertEqual(r.context['total'],20)
        self.assertNotContains(r,'movie/interstellar/')
    @patch('catalog.cloud.api',return_value=TOKENS)
    def test_login_secure_cookies_and_no_token_body(self,api):
        response=self.client.post('/account/login/',{'email':'test@example.test','password':'Long-password-123'})
        self.assertEqual(response.status_code,302)
        self.assertEqual(response['Location'],'/profile/')
        self.assertTrue(response.cookies['cs_access_token']['httponly'])
        self.assertTrue(response.cookies['cs_access_token']['secure'])
        self.assertEqual(response.cookies['cs_access_token']['samesite'],'Lax')
        self.assertNotIn(b'test-access',response.content)
        self.assertIn('no-store',response['Cache-Control'])
    @patch('catalog.cloud.api')
    def test_signup_password_validation(self,api):
        r=self.client.post('/account/signup/',{'email':'test@example.test','password':'abc','password_confirm':'abc'})
        self.assertContains(r,'от 12 до 128');api.assert_not_called()
    @patch('catalog.cloud.api',return_value={})
    def test_signup_uses_pkce(self,api):
        r=self.client.post('/account/signup/',{'email':'test@example.test','password':'Long-password-123','password_confirm':'Long-password-123','display_name':'Тест'})
        self.assertContains(r,'Откройте ссылку в этом же браузере')
        self.assertTrue(r.cookies['cs_pkce']['httponly'])
        self.assertEqual(api.call_args.kwargs['data']['code_challenge_method'],'s256')
        self.assertNotIn(api.call_args.kwargs['data']['code_challenge'],r.cookies['cs_pkce'].value)
    def test_callback_does_not_accept_arbitrary_redirect(self):
        r=self.client.get('/account/callback/?code=fake&next=https://evil.test')
        self.assertEqual(r.status_code,400)
        self.assertNotIn('Location',r)
    @patch('catalog.cloud.api',return_value=USER)
    def test_library_ownership_is_server_derived(self,api):
        self.login_client()
        def side(method,path,**kw):
            if path=='/auth/v1/user':return USER
            if method=='POST':return None
            return [kw['data']]
        api.side_effect=side
        r=self.client.post('/api/v1/library/',json.dumps({'movie_id':'interstellar','saved':True,'user_id':'attacker-id'}),content_type='application/json')
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['item']['user_id'],USER['id'])
        self.assertEqual(r.json()['item']['movie']['title'],'Интерстеллар')
        self.assertIn('no-store',r['Cache-Control'])
    @patch('catalog.cloud.api',return_value=USER)
    def test_invalid_rating_and_unknown_movie(self,api):
        self.login_client()
        for rating in [True,0,11,'8',8.5]:
            r=self.client.post('/api/v1/library/',json.dumps({'movie_id':'interstellar','rating':rating}),content_type='application/json')
            self.assertEqual(r.status_code,400)
        r=self.client.post('/api/v1/library/',json.dumps({'movie_id':'unknown','saved':True}),content_type='application/json')
        self.assertEqual(r.status_code,404)
    @patch('catalog.cloud.api')
    def test_refresh_and_service_outage(self,api):
        self.login_client();self.client.cookies['cs_refresh_token']='old-refresh'
        api.side_effect=[CloudError(401),TOKENS,[]]
        r=self.client.get('/')
        self.assertEqual(r.cookies['cs_refresh_token'].value,'test-refresh')
        api.side_effect=CloudError(503)
        r=self.client.get('/profile/')
        self.assertContains(r,'Не удалось загрузить аккаунт')
        self.assertNotIn('cs_refresh_token',r.cookies)
    @patch('catalog.cloud.api',return_value=USER)
    def test_csrf_protection(self,api):
        client=Client(enforce_csrf_checks=True);client.cookies['cs_access_token']='test-access'
        for url in ['/api/v1/library/','/account/logout/','/profile/','/account/login/']:
            self.assertEqual(client.post(url,{}).status_code,403)

    @override_settings(AUTH_EMAIL_READY=False)
    @patch('catalog.cloud.api')
    def test_unconfigured_email_is_explicit_not_broken_signup(self,api):
        r=self.client.post('/account/signup/',{'email':'test@example.test','password':'Long-password-123'})
        self.assertEqual(r.status_code,503);api.assert_not_called()
        self.assertContains(self.client.get('/account/signup/'),'Настраиваем отправку писем')
