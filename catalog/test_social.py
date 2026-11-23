import base64
import copy
import io
from unittest.mock import patch
from PIL import Image
from django.test import SimpleTestCase, Client, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from .social import avatar
from .providers import LocalProvider
from .test_accounts import USER, STORES

OTHER='7ed12a7e-48ed-41a3-a207-e168078afbb9'
RID='5ed12a7e-48ed-41a3-a207-e168078afbb9'
PROFILE={'id':USER['id'],'display_name':'Александр','bio':'Истории, к которым возвращаюсь.','avatar_data':'','cover_id':'interstellar','is_public':False,'stars':['Мэттью Макконахи']}

class FakeSocial:
    """Isolated UI/test fixtures. Production views never instantiate this class."""
    def __init__(self):
        self.user=copy.deepcopy(USER)
        self.tables={'profiles':[copy.deepcopy(PROFILE),{**PROFILE,'id':OTHER,'display_name':'Анна','is_public':True}],
         'library':[{'user_id':USER['id'],'movie_id':mid,'saved':True,'watched':i<3,'rating':9 if i<3 else None,'updated_at':'2026-10-05T00:00:00Z'} for i,mid in enumerate(['interstellar','dune-part-two','inception','the-dark-knight','whiplash'])],
         'follows':[], 'reviews':[{'id':RID,'user_id':USER['id'],'movie_id':'interstellar','body':'Редкое кино, которое хочется обсуждать ещё долго после титров.','spoiler':False,'updated_at':'2026-10-05T00:00:00Z'}], 'review_comments':[]}
        self.calls=[]
        self.user['user_metadata']['display_name']=PROFILE['display_name']
        for row in self.tables['library']: row['movie']=LocalProvider().get(row['movie_id'])
    def __call__(self,method,path,**kw):
        self.calls.append((method,path,copy.deepcopy(kw)))
        if path=='/auth/v1/user':
            if method=='PUT': self.user['user_metadata'].update(kw.get('data',{}).get('data',{}))
            return copy.deepcopy(self.user)
        table=path.split('/')[-1]; rows=self.tables.get(table,[]);params=kw.get('params',{})
        def match(row):
            for k,v in params.items():
                if k in ('select','order','limit','offset','on_conflict'):continue
                if v.startswith('eq.') and str(row.get(k)).lower()!=v[3:].lower():return False
                if v.startswith('neq.') and str(row.get(k))==v[4:]:return False
                if v.startswith('in.') and str(row.get(k)) not in v[4:-1].split(','):return False
                if v=='not.is.null' and row.get(k) is None:return False
                if v.startswith('ilike.') and v[7:-1].lower() not in str(row.get(k,'')).lower():return False
            return True
        if method=='GET':return copy.deepcopy([r for r in rows if match(r)])
        if method=='PATCH':
            for r in rows:
                if match(r):r.update(kw['data'])
            return None
        if method=='DELETE':self.tables[table]=[r for r in rows if not match(r)];return None
        if method=='POST':
            row=copy.deepcopy(kw['data']);row.setdefault('id',RID)
            if table=='reviews':
                old=next((r for r in rows if r['user_id']==row['user_id'] and r['movie_id']==row['movie_id']),None)
                if old:old.update(row);return [old]
            rows.append(row);return [row]

@override_settings(ALLOWED_HOSTS=['testserver'],STORAGES=STORES,SUPABASE_URL='https://test.supabase.co',SUPABASE_PUBLISHABLE_KEY='test')
class SocialTests(SimpleTestCase):
    def setUp(self):
        self.fake=FakeSocial();self.api=patch('catalog.cloud.api',side_effect=self.fake).start();self.addCleanup(patch.stopall)
        self.client.cookies['cs_access_token']='test-access'
    def test_all_profile_tabs_render_real_data(self):
        for tab in ['overview','saved','watched','ratings','settings','reviews','comments','friends','people','followers','following','stars']:
            r=self.client.get('/profile/',{'tab':tab});self.assertEqual(r.status_code,200,tab)
            self.assertContains(r,'Александр');self.assertIn('no-store',r['Cache-Control'])
        r=self.client.get('/profile/?tab=ratings');self.assertContains(r,'Моя оценка · 9/10')
    def test_public_profile_hides_email_and_editing(self):
        r=self.client.get('/people/'+OTHER+'/');self.assertEqual(r.status_code,200)
        self.assertNotContains(r,'test@example.test');self.assertNotContains(r,'Редактировать профиль')
        self.assertEqual(self.client.post('/people/'+OTHER+'/',{'display_name':'Hacked'}).status_code,404)
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/people/'+USER['id']+'/').status_code,404)
    def test_settings_save_and_default_private(self):
        r=self.client.post('/profile/?tab=settings',{'display_name':'Новое имя','bio':'Мои фильмы','cover_id':'dune-part-two'})
        self.assertEqual(r.status_code,302)
        self.assertEqual(self.fake.tables['profiles'][0]['display_name'],'Новое имя')
        self.assertFalse(self.fake.tables['profiles'][0]['is_public'])
    def test_invalid_settings_preserve_existing(self):
        r=self.client.post('/profile/?tab=settings',{'display_name':'','cover_id':'bad'})
        self.assertContains(r,'Имя должно');self.assertEqual(self.fake.tables['profiles'][0]['display_name'],'Александр')
    def test_follow_and_unfollow_use_authenticated_owner(self):
        self.assertEqual(self.client.post('/people/'+OTHER+'/follow/',{'follower_id':OTHER}).status_code,302)
        self.assertEqual(self.fake.tables['follows'][0]['follower_id'],USER['id'])
        self.client.post('/people/'+OTHER+'/follow/',{'action':'unfollow'})
        self.assertEqual(self.fake.tables['follows'],[])
        self.client.post('/people/'+USER['id']+'/follow/')
        self.assertEqual(self.fake.tables['follows'],[])
    def test_review_save_escape_and_delete(self):
        body='<script>alert(1)</script> Это мои впечатления о фильме.'
        self.client.post('/profile/?tab=reviews',{'action':'review','movie_id':'interstellar','body':body,'user_id':OTHER,'spoiler':'on'})
        self.assertEqual(len(self.fake.tables['reviews']),1)
        row=self.fake.tables['reviews'][0];self.assertEqual(row['user_id'],USER['id']);self.assertTrue(row['spoiler'])
        r=self.client.get('/reviews/'+RID+'/');self.assertNotContains(r,'<script>alert(1)</script>');self.assertContains(r,'&lt;script&gt;')
        self.client.post('/profile/?tab=reviews',{'action':'delete_review','review_id':RID})
        self.assertEqual(self.fake.tables['reviews'],[])
    def test_private_profile_cannot_comment(self):
        self.client.post('/reviews/'+RID+'/',{'body':'Комментарий'})
        self.assertEqual(self.fake.tables['review_comments'],[])
        self.fake.tables['profiles'][0]['is_public']=True
        self.client.post('/reviews/'+RID+'/',{'body':'Комментарий'})
        self.assertEqual(self.fake.tables['review_comments'][0]['user_id'],USER['id'])
    def test_own_comment_can_be_deleted_when_review_is_unavailable(self):
        self.fake.tables['reviews']=[]
        self.fake.tables['review_comments']=[{'id':RID,'review_id':OTHER,'user_id':USER['id'],
            'body':'Мой комментарий','created_at':'2026-10-05T00:00:00Z'}]
        r=self.client.get('/profile/?tab=comments')
        self.assertContains(r,'Обсуждение стало недоступно')
        self.assertNotContains(r,'action="/reviews/'+OTHER+'/"')
        r=self.client.post('/profile/?tab=comments',{'action':'delete_comment','comment_id':RID,'user_id':OTHER})
        self.assertEqual(r.status_code,302)
        self.assertEqual(self.fake.tables['review_comments'],[])
        call=next(c for c in self.fake.calls if c[:2]==('DELETE','/rest/v1/review_comments'))
        self.assertEqual(call[2]['params']['user_id'],'eq.'+USER['id'])
    def test_csrf_for_new_mutations(self):
        c=Client(enforce_csrf_checks=True);c.cookies['cs_access_token']='test-access'
        for url in ['/people/'+OTHER+'/follow/','/reviews/'+RID+'/']:
            self.assertEqual(c.post(url,{}).status_code,403)
    def test_invalid_actor_is_rejected(self):
        r=self.client.post('/profile/?tab=stars',{'action':'stars','stars':['invented actor']})
        self.assertContains(r,'Выберите до 24 актёров')
    def test_avatar_reencodes_and_rejects_non_image(self):
        raw=io.BytesIO();Image.new('RGB',(900,400),'blue').save(raw,format='PNG')
        result=avatar(SimpleUploadedFile('photo.png',raw.getvalue()))
        im=Image.open(io.BytesIO(base64.b64decode(result.split(',')[1])))
        self.assertEqual(im.size,(256,256));self.assertEqual(im.format,'JPEG')
        with self.assertRaises(ValidationError):avatar(SimpleUploadedFile('bad.svg',b'<svg/>'))
