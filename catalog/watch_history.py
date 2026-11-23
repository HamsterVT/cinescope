"""Account-owned playback checkpoints. The caller's JWT is the only write authority."""
import json
from datetime import datetime, timedelta, timezone

from django.http import HttpResponse, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from . import cloud
from .providers import LocalProvider, ProviderError, get_provider

TABLE = '/rest/v1/watch_progress'
FIELDS = 'movie_id,position_seconds,duration_seconds,observed_at'


def for_account(request, *, movie_id=None, limit=30):
    params = {'select': FIELDS, 'user_id': 'eq.' + request.cloud_user['id'],
              'order': 'observed_at.desc', 'limit': str(limit)}
    if movie_id:
        params['movie_id'] = 'eq.' + movie_id
    return cloud.api('GET', TABLE, token=request.cloud_token, params=params) or []


def resume_position(row):
    position = row['position_seconds']
    return position if 5 <= position < row['duration_seconds'] - 8 else 0


def position_label(seconds):
    minutes, seconds = divmod(seconds, 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f'{hours}:{minutes:02d}:{seconds:02d}'
    return f'{minutes}:{seconds:02d}'


def entries(request, limit):
    from .playback import media_for
    rows = []
    provider = get_provider()
    for row in for_account(request, limit=limit):
        movie_id = row['movie_id']
        movie = LocalProvider().get(movie_id)
        if not movie:
            try:
                movie = provider.get(movie_id)
            except ProviderError:
                continue
        if movie:
            position = resume_position(row)
            rows.append({**row, 'movie': movie, 'resumable': position,
                         'position_label': position_label(position),
                         'complete': row['position_seconds'] >= row['duration_seconds'] - 8,
                         'has_media': bool(media_for(request, movie_id)),
                         'percent': round(100 * row['position_seconds'] / row['duration_seconds'])})
    return rows


@never_cache
@require_POST
def record(request, movie_id):
    if not request.cloud_user:
        return JsonResponse({'error': 'Требуется вход.'}, status=401)
    if request.content_type != 'application/json' or len(movie_id) > 100:
        return JsonResponse({'error': 'Некорректный запрос.'}, status=400)
    try:
        body = json.loads(request.read(2049))
        position, duration, observed = body['position'], body['duration'], body['observed_at']
        if type(position) is not int or type(duration) is not int or type(observed) is not int:
            raise ValueError
        if not 1 <= duration <= 86400 or not 0 <= position <= duration:
            raise ValueError
        recorded_at = datetime.fromtimestamp(observed / 1000, tz=timezone.utc)
        now = datetime.now(timezone.utc)
        if abs(recorded_at - now) > timedelta(minutes=5):
            recorded_at = now
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return JsonResponse({'error': 'Некорректная позиция.'}, status=400)

    from .playback import media_for
    try:
        if not media_for(request, movie_id):
            return JsonResponse({'error': 'Видео недоступно.'}, status=404)
        cloud.api('POST', '/rest/v1/rpc/record_watch_progress', token=request.cloud_token, data={
            'p_movie_id': movie_id, 'p_position': position, 'p_duration': duration,
            'p_observed_at': recorded_at.isoformat(),
        })
    except cloud.CloudError:
        return JsonResponse({'error': 'Не удалось сохранить позицию.'}, status=503)
    return HttpResponse(status=204)
