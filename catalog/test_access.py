import copy
from unittest.mock import patch
from django.core import signing
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, override_settings, Client
from .test_accounts import USER, TOKENS, STORES
from .test_social import FakeSocial, RID
from .phone import normalize_phone
from .passwords import validate_password

@override_settings(ALLOWED_HOSTS=['testserver'],STORAGES=STORES,SUPABASE_URL='https://test.supabase.co',SUPABASE_PUBLISHABLE_KEY='test',AUTH_EMAIL_READY=True)
class AccessTests(SimpleTestCase):
    def test_collection_filters_intersect_instead_of_resetting(self):
        data=self.client.get('/api/v1/movies/',{'collection':'space','type':'tv','rating':'9'}).json()
        self.assertEqual(data['count'],0)
        data=self.client.get('/api/v1/movies/',{'collection':'space','genre':'фантастика','country':'США','year':'2014','rating':'8'}).json()
        self.assertEqual([m['id'] for m in data['results']],['interstellar'])
        r=self.client.get('/catalog/',{'genre':'фантастика','rating':'8'})
        self.assertContains(r,'value="Фантастика" selected')
        self.assertContains(r,'<option value="8" selected>От 8</option>',html=True)
    def test_series_reset_preserves_section(self):
        r=self.client.get('/series/',{'genre':'Драма','rating':'9'})
        self.assertContains(r,'href="/series/">Сбросить фильтры')
        self.assertTrue(all(m['media_type']=='tv' for m in r.context['movies']))
    def test_strong_password_and_unicode_policy(self):
        for pw in ['abcdefghijklmnop','ABCDEFGHIJK123!','abcdefghijk123!','Abcdefghijklm!','Abcdefghijk123','Short1!']:
            with self.assertRaises(ValidationError): validate_password(pw)
        with self.assertRaises(ValidationError):validate_password('СильныйПароль123!')
        with self.assertRaises(ValidationError):validate_password('StrongPassword123🙂')
        validate_password('СильныйПарольAa123!')
    @patch('catalog.cloud.api',return_value=TOKENS)
    def test_existing_passwords_can_still_log_in(self,api):
        self.assertEqual(self.client.post('/account/login/',{'email':'test@example.test','password':'oldpass'}).status_code,302)
    @patch('catalog.cloud.api')
    def test_weak_new_password_is_rejected_server_side(self,api):
        self.client.post('/account/signup/',{'email':'test@example.test','password':'abcdefghijklmnop'})
        api.assert_not_called()
    def test_phone_normalization(self):
        self.assertEqual(normalize_phone('8 (701) 123-45-67'),'+77011234567')
        self.assertEqual(normalize_phone('+7 701 123 45 67'),'+77011234567')
        for value in ['abc','+7 123','+7/7011234567','0'*11]:
            with self.assertRaises(ValidationError): normalize_phone(value)
    @patch('catalog.cloud.api')
    def test_unconfigured_phone_is_honest_and_does_not_send(self,api):
        r=self.client.post('/account/phone/?mode=signup',{'phone':'+77011234567','password':'Test-password123!'})
        self.assertEqual(r.status_code,503);api.assert_not_called()
        self.assertContains(self.client.get('/account/phone/'),'Продолжить с email')
    @override_settings(AUTH_PHONE_READY=True)
    @patch('catalog.cloud.api')
    def test_whatsapp_signup_and_verified_secure_session(self,api):
        api.return_value={}
        r=self.client.post('/account/phone/?mode=signup',{'phone':'8 701 123 45 67','password':'Test-password123!'})
        self.assertEqual(r.status_code,302)
        self.assertEqual(api.call_args.kwargs['data']['channel'],'whatsapp')
        cookie=r.cookies['cs_phone'];self.assertTrue(cookie['httponly'])
        state=signing.loads(cookie.value,salt='phone-otp');self.assertEqual(state['phone'],'+77011234567')
        self.assertNotIn('password',state)
        api.return_value=TOKENS
        r=self.client.post('/account/phone/?mode=signup',{'action':'verify','phone':'+19999999999','code':'123456'})
        self.assertEqual(r['Location'],'/profile/')
        self.assertEqual(api.call_args.kwargs['data'],{'phone':'+77011234567','token':'123456','type':'sms'})
        self.assertTrue(r.cookies['cs_access_token']['httponly']);self.assertTrue(r.cookies['cs_access_token']['secure'])
        self.assertEqual(r.cookies['cs_phone']['max-age'],0)
    @override_settings(AUTH_PHONE_READY=True)
    @patch('catalog.cloud.api',return_value={})
    def test_phone_login_never_silently_creates_account_and_limits_resend(self,api):
        self.client.post('/account/phone/',{'phone':'+77011234567'})
        self.assertFalse(api.call_args.kwargs['data']['create_user'])
        api.reset_mock();r=self.client.post('/account/phone/',{'action':'resend'})
        api.assert_not_called();self.assertContains(r,'через минуту')
    @override_settings(AUTH_PHONE_READY=True)
    @patch('catalog.cloud.api')
    def test_tampered_pending_state_cannot_verify(self,api):
        self.client.cookies['cs_phone']='forged';self.client.post('/account/phone/',{'action':'verify','code':'123456'})
        api.assert_not_called()
        c=Client(enforce_csrf_checks=True)
        self.assertEqual(c.post('/account/phone/',{'phone':'+77011234567'}).status_code,403)
    def test_user_metadata_cannot_escalate_role(self):
        fake=FakeSocial();fake.user['user_metadata']['role']='admin';fake.tables['user_roles']=[]
        self.client.cookies['cs_access_token']='test-access'
        with patch('catalog.cloud.api',side_effect=fake): self.assertEqual(self.client.get('/manage/').status_code,403)
    def test_admin_can_hide_and_restore_public_content(self):
        fake=FakeSocial();fake.tables['user_roles']=[{'user_id':USER['id'],'role':'admin'}]
        fake.tables['content_visibility']=[];fake.tables['profiles'][0]['is_public']=True
        self.client.cookies['cs_access_token']='test-access'
        with patch('catalog.cloud.api',side_effect=fake):
            self.assertContains(self.client.get('/manage/'),'Управление CineScope')
            r=self.client.post('/manage/',{'kind':'review','target':RID,'action':'hide'})
            self.assertEqual(r.status_code,302)
            call=[c for c in fake.calls if c[0]=='POST' and c[1]=='/rest/v1/content_visibility'][-1]
            self.assertTrue(call[2]['data']['hidden']);self.assertEqual(call[2]['data']['changed_by'],USER['id'])
    def test_guest_manage_redirects_to_login(self):
        self.assertEqual(self.client.get('/manage/')['Location'],'/account/login/')
