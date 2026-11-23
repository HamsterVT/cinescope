from unittest.mock import patch
import time

from django.core.cache import cache
from django.test import Client, RequestFactory, SimpleTestCase, override_settings

from . import admin_views, featured, playback, watch_history
from .test_accounts import STORES


@override_settings(VIDEO_PLAYBACK_ENABLED=True, STORAGES=STORES, ALLOWED_HOSTS=['testserver'])
class PlaybackTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.source = {
            'movie_id': 'dune-part-two', 'movie_title': 'Дюна: Часть вторая',
            'movie_year': 2024, 'source_url': 'https://cinema.example/dune',
            'source_name': 'Example Cinema', 'media_url': '',
        }

    def test_builtin_library_full_player_and_scoped_policy(self):
        detail = self.client.get('/movie/big-buck-bunny/')
        self.assertContains(detail, 'href="/watch/big-buck-bunny/"')
        self.assertNotIn('upload.wikimedia.org', detail['Content-Security-Policy'])
        guest = self.client.get('/watch/big-buck-bunny/')
        self.assertContains(guest, 'Войдите для просмотра')
        self.assertNotContains(guest, '<video')
        self.assertNotContains(guest, 'upload.wikimedia.org')
        self.assertIn('no-store', guest['Cache-Control'])
        request = self.player_request('big-buck-bunny')
        watch = playback.watch(request, 'big-buck-bunny')
        watch = self.add_headers_for_test(request, watch)
        self.assertContains(watch, '<video controls playsinline preload="none"')
        self.assertContains(watch, 'video/webm')
        self.assertContains(watch, 'CC BY 3.0')
        self.assertIn("media-src 'self' https://upload.wikimedia.org", watch['Content-Security-Policy'])
        self.assertNotIn('vidsrc', watch['Content-Security-Policy'])
        self.assertContains(self.client.get('/watch/'), 'Синтел')

    def player_request(self, movie_id, *, user_id='test-user', params=None):
        request = RequestFactory().get('/watch/' + movie_id + '/', params or {})
        request.cloud_user = {'id': user_id}
        request.cloud_token = 'user-token'
        request.cinescope_role = 'user'
        return request

    def add_headers_for_test(self, request, response):
        from .middleware import HeadersMiddleware
        return HeadersMiddleware(lambda request: response)(request)

    def test_title_without_source_has_trailer_without_full_play_button(self):
        detail = self.client.get('/movie/dune-part-two/')
        self.assertContains(detail, 'Полная версия пока недоступна')
        self.assertNotContains(detail, 'href="/watch/dune-part-two/"')
        self.assertContains(self.client.get('/watch/dune-part-two/'), 'Полной версии пока нет', status_code=404)

    def test_source_is_public_only_when_requested_and_official_link_needs_no_frame(self):
        with patch('catalog.playback.cloud.configured', return_value=True), \
                patch('catalog.playback.cloud.api', return_value=[self.source]) as get:
            detail = self.client.get('/movie/dune-part-two/')
            self.assertContains(detail, 'Где смотреть')
            watch = playback.watch(self.player_request('dune-part-two'), 'dune-part-two')
            self.assertContains(watch, 'https://cinema.example/dune')
            self.assertNotContains(watch, '<video')
            self.assertEqual(get.call_count, 1)
            self.assertEqual(get.call_args.kwargs['params']['published'], 'eq.true')
            self.assertNotIn('token', get.call_args.kwargs)

    @override_settings(FEATURED_DEMO_USERS={'approved'},
                       FEATURED_DEMO_MP4='https://magic.host.cinemap.cc/demo/test.mp4')
    def test_demo_url_only_appears_for_the_screening_account(self):
        for user_id in ('test-user', 'approved'):
            watch = playback.watch(self.player_request(featured.ID, user_id=user_id), featured.ID)
            if user_id == 'approved':
                self.assertContains(watch, '<video')
                self.assertContains(watch, 'demo/test.mp4')
            else:
                self.assertNotContains(watch, 'demo/test.mp4')
                self.assertContains(watch, 'Sony Pictures')
        self.assertNotContains(self.client.get('/watch/' + featured.ID + '/'), 'demo/test.mp4')

    def test_signin_returns_to_the_requested_film(self):
        from django.shortcuts import redirect
        from .cloud import CloudSessionMiddleware, set_tokens
        response = self.client.get('/watch/sintel/?signin=1')
        self.assertEqual(response['Location'], '/account/login/')
        request = RequestFactory().get('/account/login/')
        request.COOKIES['cs_watch_return'] = response.cookies['cs_watch_return'].value
        middleware = CloudSessionMiddleware(lambda request: set_tokens(redirect('/profile/'),
            {'access_token': 'access', 'refresh_token': 'refresh'}))
        self.assertEqual(middleware(request)['Location'], '/watch/sintel/')
        request.COOKIES['cs_watch_return'] = 'https://outside.example'
        self.assertEqual(middleware(request)['Location'], '/profile/')

    def test_progress_uses_caller_jwt_and_validates_position(self):
        data = {'position': 71, 'duration': 640, 'observed_at': round(time.time() * 1000), 'user_id': 'other'}
        def make_request(body):
            request = RequestFactory().post('/api/v1/watch-progress/big-buck-bunny/', body, content_type='application/json')
            request.cloud_user = {'id': 'user1'}
            request.cloud_token = 'private-user-token'
            return request
        with patch('catalog.watch_history.cloud.api') as send:
            response = watch_history.record(make_request(data), 'big-buck-bunny')
            self.assertEqual(response.status_code, 204)
            self.assertEqual(send.call_args.kwargs['token'], 'private-user-token')
            self.assertNotIn('user_id', send.call_args.kwargs['data'])
            self.assertEqual(send.call_args.kwargs['data']['p_position'], 71)
            self.assertEqual(watch_history.record(make_request({**data, 'position': 999}), 'big-buck-bunny').status_code, 400)
            self.assertEqual(send.call_count, 1)
        self.assertEqual(self.client.post('/api/v1/watch-progress/sintel/', data, content_type='application/json').status_code, 401)
        self.assertEqual(Client(enforce_csrf_checks=True).post('/api/v1/watch-progress/sintel/', data, content_type='application/json').status_code, 403)

    @override_settings(VIDEO_MEDIA_HOSTS=('cdn.example',))
    def test_only_configured_https_hosts_and_formats_can_supply_video(self):
        self.assertEqual(playback.media_info('https://cdn.example/movies/title.MP4')['mime'], 'video/mp4')
        for url in ('https://evil.example/movie.mp4', 'https://cdn.example@evil.example/a.mp4',
                    'https://cdn.example/a.mp4#fragment', 'https://cdn.example/file.html',
                    'http://cdn.example/a.mp4', 'https://cdn.example/a\\name.mp4'):
            with self.subTest(url=url):
                self.assertIsNone(playback.media_info(url))

    def test_management_writes_are_role_checked_and_csrf_protected(self):
        self.assertEqual(self.client.get('/manage/videos/').status_code, 302)
        self.assertEqual(Client(enforce_csrf_checks=True).post('/manage/videos/', {}).status_code, 403)
        request = RequestFactory().post('/manage/videos/', data={**self.source, 'authorized': 'on'})
        request.cloud_user = {'id': 'some-user'}
        request.cloud_token = 'user-token'
        request.cinescope_role = 'user'
        with patch('catalog.admin_views.cloud.api') as cloud:
            self.assertEqual(admin_views.video_sources(request).status_code, 403)
            cloud.assert_not_called()
        request.cinescope_role = 'admin'
        with patch('catalog.admin_views.cloud.api', return_value=[]) as cloud:
            response = admin_views.video_sources(request)
            self.assertEqual(response.status_code, 302)
            cloud_write = cloud.call_args
            self.assertEqual(cloud_write.args, ('POST', playback.SOURCE_TABLE))
            self.assertEqual(cloud_write.kwargs['token'], 'user-token')
            self.assertFalse(cloud_write.kwargs['data']['published'])

    def test_billing_posts_require_csrf(self):
        response = Client(enforce_csrf_checks=True).post('/billing/checkout/', {'plan': 'monthly'})
        self.assertEqual(response.status_code, 403)
