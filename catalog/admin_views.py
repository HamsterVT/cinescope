"""Small cloud administration surface with the caller's JWT and database RLS."""
from datetime import datetime, timezone
from uuid import UUID
from django import forms
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.shortcuts import render, redirect
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from . import cloud, playback
from .roles import get_role
from .social import query, enrich_reviews
from .providers import LocalProvider, ProviderError, get_provider

@require_http_methods(['GET','POST'])
def dashboard(request):
    if not request.cloud_user: return redirect('/account/login/')
    ctx={'active':'profile'}
    try:
        if get_role(request)!='admin':
            return render(request,'error.html',{'message':'Этот раздел доступен администратору CineScope.','code':'403'},status=403)
        if request.method=='POST':
            kind=request.POST.get('kind','')
            if kind not in ('review','comment'): raise ValidationError('Неизвестный тип записи.')
            if request.POST.get('action') not in ('hide','show'): raise ValidationError('Неизвестное действие.')
            target=str(UUID(request.POST.get('target','')))
            table='reviews' if kind=='review' else 'review_comments'
            # RLS only exposes public content to administrators; private lists stay private.
            rows=query(request,table,id='eq.'+target)
            if not rows: raise ValidationError('Запись больше не доступна.')
            author=query(request,'profiles',id='eq.'+rows[0]['user_id'],is_public='eq.true')
            if not author: raise ValidationError('Публикация в закрытом профиле недоступна для модерации.')
            cloud.api('POST','/rest/v1/content_visibility',token=request.cloud_token,
                params={'on_conflict':'kind,target'},prefer='resolution=merge-duplicates',
                data={'kind':kind,'target':target,'hidden':request.POST.get('action')=='hide',
                      'changed_by':request.cloud_user['id'],'updated_at':datetime.now(timezone.utc).isoformat()})
            return redirect('/manage/?done=1')
        visibility={(r['kind'],r['target']):r['hidden'] for r in query(request,'content_visibility')}
        reviews=[r for r in enrich_reviews(request,query(request,'reviews',order='updated_at.desc',limit='100')) if r['author'].get('is_public')]
        comments=query(request,'review_comments',order='created_at.desc',limit='100')
        if comments:
            author_ids={c['user_id'] for c in comments}
            public_authors={p['id'] for p in query(request,'profiles',id='in.('+','.join(author_ids)+')',is_public='eq.true')}
            comments=[c for c in comments if c['user_id'] in public_authors]
        for r in reviews: r['moderation_hidden']=visibility.get(('review',r['id']),False)
        for c in comments: c['moderation_hidden']=visibility.get(('comment',c['id']),False)
        ctx.update(reviews=reviews,comments=comments,movie_count=len(LocalProvider().search()),
            phone_ready=settings.AUTH_PHONE_READY,telegram_ready=settings.AUTH_TELEGRAM_READY,source=settings.MOVIE_PROVIDER,
            success='Видимость записи изменена.' if request.GET.get('done') else '')
        return render(request,'manage.html',ctx)
    except (ValidationError,ValueError) as exc:
        return render(request,'error.html',{'message':' '.join(exc.messages) if isinstance(exc,ValidationError) else 'Некорректная запись.'},status=400)
    except (cloud.CloudError,ProviderError):
        return render(request,'error.html',{'message':'Не удалось загрузить панель управления. Обновите страницу позже.'},status=503)



class VideoSourceForm(forms.Form):
    movie_id = forms.RegexField(r'^[a-z0-9][a-z0-9-]{0,99}$', label='Карточка фильма',
                                widget=forms.TextInput(attrs={'list': 'catalog-titles'}))
    source_name = forms.CharField(max_length=120, label='Студия или сервис')
    source_url = forms.URLField(assume_scheme='https', max_length=1000, label='Официальная страница фильма')
    media_url = forms.URLField(assume_scheme='https', max_length=1000, required=False,
                               label='Ссылка на MP4 или WebM')
    audio_label = forms.CharField(max_length=120, required=False, label='Звук')
    published = forms.BooleanField(required=False, label='Опубликовать')
    authorized = forms.BooleanField(label='У CineScope есть разрешение на выбранный способ показа.',
                                   required=True)

    def clean_source_url(self):
        url = self.cleaned_data['source_url']
        if not playback.https_origin(url):
            raise ValidationError('Нужна публичная HTTPS-ссылка.')
        return url

    def clean_media_url(self):
        url = self.cleaned_data['media_url']
        if url and not playback.media_info(url):
            raise ValidationError('Укажите прямую HTTPS-ссылку на MP4 или WebM с разрешённого хоста.')
        return url


@never_cache
@require_http_methods(['GET', 'POST'])
def video_sources(request):
    if not request.cloud_user:
        return redirect('/account/login/')
    template = 'manage_videos.html'
    ctx = {'active': 'profile', 'movies': LocalProvider().search(),
           'media_hosts': settings.VIDEO_MEDIA_HOSTS, 'form': VideoSourceForm()}
    try:
        if get_role(request) != 'admin':
            return render(request, 'error.html', {'message': 'Этот раздел доступен администратору CineScope.', 'code': '403'}, status=403)
        rows = cloud.api('GET', playback.SOURCE_TABLE, token=request.cloud_token, params={'order': 'movie_title.asc'})
        ctx['sources'] = rows
        if request.method == 'POST':
            form = VideoSourceForm(request.POST)
            ctx['form'] = form
            if form.is_valid():
                data = dict(form.cleaned_data)
                del data['authorized']
                movie = get_provider().get(data['movie_id']) or LocalProvider().get(data['movie_id'])
                if not movie or movie.get('media_type') == 'tv':
                    form.add_error('movie_id', 'Выберите существующий фильм. Серии пока не подключаются через эту форму.')
                else:
                    cloud.api('POST', playback.SOURCE_TABLE, token=request.cloud_token,
                              params={'on_conflict': 'movie_id'}, prefer='resolution=merge-duplicates',
                              data={**data, 'movie_title': movie['title'][:250],
                                    'movie_year': movie['year'] or None,
                                    'updated_at': datetime.now(timezone.utc).isoformat()})
                    cache.delete(playback.SOURCE_CACHE_KEY)
                    return redirect('/manage/videos/?saved=1')
            return render(request, template, ctx, status=400)
        selected = next((row for row in rows if row['movie_id'] == request.GET.get('movie')), None)
        ctx['form'] = VideoSourceForm(initial=selected)
        ctx['saved'] = request.GET.get('saved') == '1'
        return render(request, template, ctx)
    except (cloud.CloudError, ProviderError):
        return render(request, 'error.html', {'message': 'Не удалось загрузить видеоисточники. Попробуйте немного позже.'}, status=503)
