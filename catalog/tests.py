import json
from unittest.mock import patch
import httpx
from django.test import TestCase, SimpleTestCase, override_settings
from django.core.cache import cache
from .providers import LocalProvider, TMDbProvider, DatabaseProvider, ProviderError
from .models import Movie

@override_settings(ALLOWED_HOSTS=['testserver'], STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class CatalogPages(SimpleTestCase):
    def test_guest_landing_and_authenticated_home(self):
        from django.test import RequestFactory
        from . import views
        for url, template in [('/', 'guest.html'), ('/subscription/', 'subscription.html')]:
            response = self.client.get(url)
            self.assertTemplateUsed(response, template)
            self.assertTemplateUsed(response, 'partials/navigation.html')
            self.assertContains(response, '15 990 ₸')
        request = RequestFactory().get('/')
        request.cloud_user = {'id': 'test-user'}
        with patch('catalog.views.render') as render:
            views.home(request)
            self.assertEqual(render.call_args.args[1], 'guest.html')
            views.subscription(request)
            self.assertEqual(render.call_args.args[1], 'subscription.html')

    def test_shared_navigation_and_store(self):
        for url in ['/store/', '/catalog/', '/trailers/', '/profile/', '/account/login/', '/account/signup/', '/devices/', '/series/', '/collections/']:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertTemplateUsed(response, 'partials/navigation.html')
            self.assertContains(response, 'id="cs-drawer"', count=1)
            self.assertNotContains(response, 'class="sidebar"')
        response = self.client.get('/store/')
        self.assertGreaterEqual(len(response.context['slides']), 2)
        self.assertContains(response, 'Доступные полные фильмы')
        self.assertEqual(self.client.post('/store/').status_code, 405)

    def test_public_pages_and_security(self):
        for url in ['/', '/catalog/', '/movie/dune-part-two/', '/watchlist/', '/collections/', '/about/']:
            r=self.client.get(url)
            self.assertEqual(r.status_code,200,url)
            self.assertContains(r,'CineScope')
            self.assertIn("object-src 'none'",r.headers['Content-Security-Policy'])
    def test_search_russian_english_director(self):
        for query,slug in [('ИНТЕРСТЕЛЛАР','interstellar'),('interstellar','interstellar'),('Вильнев','dune-part-two')]:
            data=self.client.get('/api/v1/movies/',{'q':query}).json()
            self.assertIn(slug,[m['id'] for m in data['results']])
    def test_filter_sort_pagination(self):
        r=self.client.get('/catalog/',{'genre':'Драма','year':'2023'})
        self.assertContains(r,'Прошлые жизни');self.assertNotContains(r,'movie/interstellar/')
        data=self.client.get('/api/v1/movies/',{'sort':'rating'}).json()
        ratings=[m['rating'] for m in data['results']]
        self.assertEqual(ratings,sorted(ratings,reverse=True))
        self.assertContains(self.client.get('/catalog/',{'page':2}),'Прибытие')
        self.assertEqual(self.client.get('/catalog/',{'page':'bad'}).status_code,200)
    def test_filter_clear_preserves_query_and_handles_malformed_year(self):
        response = self.client.get('/catalog/', {'q': 'Дюна', 'genre': 'ФАНТАСТИКА', 'year': '²²²²'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['genre'], 'Фантастика')
        self.assertEqual(response.context['year'], '')
        self.assertEqual(response.context['active_filters'][0]['url'], '/catalog/?q=%D0%94%D1%8E%D0%BD%D0%B0&year=%C2%B2%C2%B2%C2%B2%C2%B2')
        self.assertEqual(response.context['reset_url'], '/catalog/?q=%D0%94%D1%8E%D0%BD%D0%B0')
        self.assertEqual(self.client.get('/api/v1/movies/', {'watch': '1'}).json()['count'], 4)

    def test_collection_and_empty_search(self):
        data=self.client.get('/api/v1/movies/',{'collection':'space'}).json()
        self.assertEqual(data['count'],5)
        self.assertEqual(self.client.get('/api/v1/movies/',{'q':'no-such-film-123'}).json()['count'],0)
        self.assertContains(self.client.get('/catalog/',{'q':'no-such-film-123'}),'Ничего не найдено')
    def test_404_and_xss_and_get_only(self):
        self.assertEqual(self.client.get('/movie/unknown/').status_code,404)
        self.assertEqual(self.client.get('/unknown/').status_code,404)
        r=self.client.get('/catalog/',{'q':'<script>alert(1)</script>'})
        self.assertNotContains(r,'<script>alert(1)</script>')
        self.assertEqual(self.client.post('/api/v1/movies/').status_code,405)
    @override_settings(MOVIE_PROVIDER='tmdb',TMDB_READ_TOKEN='')
    def test_missing_source_fallback(self):
        data=self.client.get('/api/v1/movies/',{'q':'Дюна'}).json()
        self.assertTrue(data['fallback']);self.assertEqual(data['count'],2)
    @override_settings(MOVIE_PROVIDER='tmdb',TMDB_READ_TOKEN='test')
    def test_tmdb_detail_failure_is_503(self):
        with patch('catalog.providers.httpx.get',side_effect=httpx.ConnectError('offline')):
            self.assertEqual(self.client.get('/movie/tmdb-9999999/').status_code,503)

@override_settings(TMDB_READ_TOKEN='test')
class TMDbTests(SimpleTestCase):
    def setUp(self):cache.clear()
    def test_request_auth_cache_and_normalization(self):
        result={'results':[{'id':42,'title':'Тест','original_title':'Test','release_date':'2024-01-01','vote_average':8.123,'genre_ids':[18],'poster_path':'/poster.jpg'}]}
        response=httpx.Response(200,json=result,request=httpx.Request('GET','https://api.themoviedb.org/3/search/movie'))
        with patch('catalog.providers.httpx.get',return_value=response) as get:
            movies=TMDbProvider().search('test');TMDbProvider().search('test')
            self.assertEqual(get.call_count,1)
            self.assertEqual(get.call_args.kwargs['headers']['Authorization'],'Bearer test')
            self.assertEqual(movies[0]['id'],'tmdb-42');self.assertEqual(movies[0]['rating'],8.1)
            self.assertEqual(movies[0]['genres'],['Драма'])
    def test_timeout_is_controlled(self):
        with patch('catalog.providers.httpx.get',side_effect=httpx.ReadTimeout('timeout')):
            with self.assertRaises(ProviderError):TMDbProvider().search('timeout')
    def test_not_found(self):
        response=httpx.Response(404,request=httpx.Request('GET','https://api.themoviedb.org/3/movie/99'))
        with patch('catalog.providers.httpx.get',return_value=response):self.assertIsNone(TMDbProvider().get('tmdb-99'))

class DatabaseTests(TestCase):
    def test_database_provider_and_idempotent_seed(self):
        from django.core.management import call_command
        call_command('seed_catalog',verbosity=0);call_command('seed_catalog',verbosity=0)
        self.assertEqual(Movie.objects.count(), len(LocalProvider().search()))
        self.assertEqual(DatabaseProvider().get('interstellar')['title'],'Интерстеллар')
        self.assertEqual(len(DatabaseProvider().search('Interstellar')),1)

class TrailerTests(SimpleTestCase):
    def test_catalog_trailers_and_sherlock_identity(self):
        from .playback import open_films
        open_ids = {movie['id'] for movie in open_films()}
        movies = [movie for movie in LocalProvider().search() if movie['id'] not in open_ids]
        self.assertEqual(len(movies), 91)
        for movie in movies:
            self.assertRegex(movie.get('trailer_id',''),r'^[A-Za-z0-9_-]{11}$',movie['id'])
        sherlock=LocalProvider().get('sherlock')
        self.assertEqual(sherlock['tmdb_id'],19885)
        self.assertIn('Sherlock',sherlock['trailer_title'])
    def test_remote_trailer_selection_ignores_clips_and_invalid_ids(self):
        movie={'id':42,'videos':{'results':[
            {'site':'YouTube','type':'Clip','key':'12345678901','official':True},
            {'site':'YouTube','type':'Trailer','key':'bad?url'},
            {'site':'YouTube','type':'Trailer','key':'abcdefghijk','official':True,'iso_639_1':'en'},
            {'site':'YouTube','type':'Trailer','key':'123456abcde','official':True,'iso_639_1':'ru'}]}}
        self.assertEqual(TMDbProvider().normalize(movie)['trailer_id'],'123456abcde')
    @override_settings(ALLOWED_HOSTS=['testserver'],STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
    def test_player_is_lazy_and_detail_has_fallback(self):
        r=self.client.get('/movie/dune-part-two/')
        self.assertContains(r,'data-cinema="U2Qp5pL3ovA"')
        self.assertContains(r,'https://www.youtube.com/watch?v=U2Qp5pL3ovA')
        self.assertNotContains(r,'<iframe')
        self.assertContains(r,'id="trailer-dialog"')
