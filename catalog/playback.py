"""Movies and editorial video sources. Only the browser requests media bytes."""
import json
import re
from functools import lru_cache
from urllib.parse import urlsplit

import httpx
from django.conf import settings
from django.core.cache import cache
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from . import cloud, featured
from .providers import LocalProvider, ProviderError, get_provider

SOURCE_TABLE = '/rest/v1/video_sources'
SOURCE_CACHE_KEY = 'playback:published:v1'
MEDIA_TYPES = {'.mp4': 'video/mp4', '.webm': 'video/webm'}


@lru_cache(maxsize=1)
def open_films():
    return json.loads((settings.BASE_DIR / 'catalog/data/open_films.json').read_text(encoding='utf-8'))


def editorial_films():
    """Export metadata to the catalog without leaking the player configuration."""
    fields = ('id', 'title', 'original_title', 'year', 'runtime', 'director', 'country', 'genres', 'overview')
    return [
        {**{field: row[field] for field in fields}, 'slug': row['id'], 'rating': 0, 'cast': [],
         'media_type': 'movie', 'source': 'editorial', 'poster': f"/static/open-films/{row['id']}.webp",
         'backdrop': f"/static/open-films/{row['id']}.webp"}
        for row in open_films()
    ]


def https_origin(value):
    try:
        if not isinstance(value, str) or not value or len(value) > 1000 or re.search(r'[\s\\\x00-\x1f\x7f]', value):
            return ''
        parsed = urlsplit(value)
        hostname = parsed.hostname or ''
        if (parsed.scheme == 'https' and parsed.netloc.lower() == hostname
                and re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', hostname) and '.' in hostname):
            return f'https://{hostname}'
    except ValueError:
        pass
    return ''


def media_info(url, *, demo=False):
    origin = https_origin(url)
    hosts = ('magic.host.cinemap.cc',) if demo else (
        'upload.wikimedia.org', 'download.blender.org', *settings.VIDEO_MEDIA_HOSTS,
    )
    if not origin or origin.removeprefix('https://') not in hosts:
        return None
    parsed = urlsplit(url)
    suffix = '.' + parsed.path.rsplit('.', 1)[-1].lower()
    if parsed.fragment or suffix not in MEDIA_TYPES:
        return None
    return {'url': url, 'origin': origin, 'mime': MEDIA_TYPES[suffix]}


def source_record(row):
    if not https_origin(row.get('source_url')):
        return None
    media = media_info(row.get('media_url')) if row.get('media_url') else None
    if row.get('media_url') and not media:
        return None
    return {'media': media, 'source_url': row['source_url'], 'source_name': row['source_name'],
            'audio_label': row.get('audio_label', ''), 'license_url': row.get('license_url', ''),
            'movie_title': row.get('movie_title', ''), 'movie_year': row.get('movie_year')}


def published_sources():
    if not settings.VIDEO_PLAYBACK_ENABLED:
        return {}
    existing = cache.get(SOURCE_CACHE_KEY)
    if existing is not None:
        return existing
    sources = {featured.ID: source_record({'source_url': featured.OFFICIAL_URL,
                                          'source_name': 'Sony Pictures'})}
    sources.update({row['id']: source_record(row) for row in open_films()})
    lifetime = 30
    if cloud.configured():
        try:
            rows = cloud.api('GET', SOURCE_TABLE, params={
                'published': 'eq.true',
                'select': 'movie_id,movie_title,movie_year,source_name,source_url,media_url,audio_label',
            }, timeout=httpx.Timeout(2.5, connect=1))
            for row in rows or ():
                source = source_record(row)
                if source:
                    sources[row['movie_id']] = source
        except cloud.CloudError:
            lifetime = 8
    cache.set(SOURCE_CACHE_KEY, sources, lifetime)
    return sources


def source_for(request, movie_id):
    source = published_sources().get(movie_id)
    if (source and movie_id == featured.ID and request.cloud_user
            and request.cloud_user['id'] in settings.FEATURED_DEMO_USERS):
        screening = media_info(settings.FEATURED_DEMO_MP4, demo=True)
        if screening:
            return {**source, 'media': screening, 'source_name': 'Показ CineScope', 'demo': True}
    return source


def media_for(request, movie_id):
    return (source_for(request, movie_id) or {}).get('media')


@require_GET
def library(request):
    sources = published_sources()
    movies = []
    for movie_id in sources:
        source = source_for(request, movie_id)
        movie = LocalProvider().get(movie_id) or {
            'id': movie_id, 'title': source['movie_title'], 'year': source['movie_year'],
        }
        if movie['title']:
            movies.append({**movie, 'video': source})
    return render(request, 'video_library.html', {'movies': movies, 'active': 'watch'})


@never_cache
@require_GET
def watch(request, movie_id):
    try:
        movie = get_provider().get(movie_id) or LocalProvider().get(movie_id)
    except ProviderError:
        movie = LocalProvider().get(movie_id)
        if not movie:
            return render(request, 'error.html', {'message': 'Карточка фильма временно недоступна.'}, status=503)
    if not movie:
        raise Http404
    video = source_for(request, movie_id)
    if video and not request.cloud_user and request.GET.get('signin') == '1':
        response = redirect('login')
        response.set_signed_cookie('cs_watch_return', movie_id, salt='watch-return', max_age=1800,
                                   httponly=True, secure=not settings.DEBUG, samesite='Lax')
        response.delete_cookie('cs_checkout', path='/', samesite='Lax')
        return response

    resume = 0
    resume_label = ''
    if request.cloud_user and video and video['media']:
        from .watch_history import for_account, position_label, resume_position
        request.video_origin = video['media']['origin']
        try:
            rows = for_account(request, movie_id=movie_id, limit=1)
            if rows:
                resume = resume_position(rows[0])
                resume_label = position_label(resume)
        except cloud.CloudError:
            pass
    return render(request, 'watch.html', {
        'movie': movie, 'video': video, 'active': 'watch', 'resume_seconds': resume,
        'resume_label': resume_label, 'auto_resume': request.GET.get('resume') == '1' and resume > 0,
    }, status=200 if video else 404)
