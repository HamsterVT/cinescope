"""Profiles and social actions use the caller's JWT; Postgres RLS is authoritative."""
import base64
import io
import re
import warnings
from datetime import datetime, timezone
from uuid import UUID
from urllib.parse import urlencode
from PIL import Image, ImageOps, UnidentifiedImageError
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import render, redirect
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from . import cloud
from .providers import LocalProvider, get_provider, ProviderError
from .passwords import validate_password

TABS = [('overview','Профиль'),('history','Мои просмотры'),('reviews','Рецензии'),('ratings','Оценки'),('comments','Комментарии'),
        ('friends','Оценки друзей'),('saved','Буду смотреть'),('watched','Просмотрено'),('stars','Звёзды')]
COVERS = ['interstellar','dune-part-two','inception','la-la-land','grand-budapest-hotel']

def query(request, table, **params):
    return cloud.api('GET','/rest/v1/'+table,token=request.cloud_token,
                     params={'select':'*',**params}) or []

def own_profile(request):
    uid=request.cloud_user['id']
    rows=query(request,'profiles',id='eq.'+uid)
    if rows: return rows[0]
    metadata=request.cloud_user.get('user_metadata',{})
    name=str(metadata.get('display_name') or metadata.get('name') or 'Киноман')[:60]
    rows=cloud.api('POST','/rest/v1/profiles',token=request.cloud_token,
        data={'id':uid,'display_name':str(name)[:60]},params={'on_conflict':'id'},
        prefer='resolution=ignore-duplicates,return=representation')
    return rows[0] if rows else query(request,'profiles',id='eq.'+uid)[0]

def avatar(upload):
    if upload.size>3*1024*1024: raise ValidationError('Фото должно быть не больше 3 МБ.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(upload) as im:
                if im.format not in ('JPEG','PNG','WEBP') or im.width*im.height>20_000_000:
                    raise ValueError()
                im=ImageOps.fit(ImageOps.exif_transpose(im).convert('RGB'),(256,256))
                out=io.BytesIO();im.save(out,format='JPEG',quality=82)
        return 'data:image/jpeg;base64,'+base64.b64encode(out.getvalue()).decode()
    except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError,Image.DecompressionBombWarning):
        raise ValidationError('Выберите фото JPEG, PNG или WebP до 20 мегапикселей.')

def movie_rows(rows):
    result=[]
    for row in rows:
        # Resolve from the trusted provider, never render user-controlled snapshot URLs.
        movie=get_provider().get(row['movie_id'])
        if movie: result.append({**movie,'personal_rating':row.get('rating'),'is_watched':row.get('watched',False)})
    return result

def enrich_reviews(request,rows):
    ids={r['user_id'] for r in rows}
    people={p['id']:p for p in query(request,'profiles',id='in.('+','.join(ids)+')')} if ids else {}
    targets=[r['id'] for r in rows]
    hidden={v['target'] for v in query(request,'content_visibility',kind='eq.review',
        hidden='eq.true',target='in.('+','.join(targets)+')')} if targets else set()
    for r in rows:
        r['author']=people.get(r['user_id'],{'display_name':'Закрытый профиль'})
        r['film']=get_provider().get(r['movie_id'])
        r['moderation_hidden']=r['id'] in hidden
    return rows

def edit_profile(request,p):
    name=request.POST.get('display_name','').strip()
    bio=request.POST.get('bio','').strip()
    cover=request.POST.get('cover_id','interstellar')
    if not 1<=len(name)<=60: raise ValidationError('Имя должно содержать от 1 до 60 символов.')
    if len(bio)>280: raise ValidationError('О себе — не больше 280 символов.')
    if cover not in COVERS: raise ValidationError('Выберите обложку из списка.')
    data={'display_name':name,'bio':bio,'cover_id':cover,'is_public':request.POST.get('is_public')=='on'}
    if request.POST.get('remove_avatar'): data['avatar_data']=''
    if request.FILES.get('avatar'): data['avatar_data']=avatar(request.FILES['avatar'])
    cloud.api('PATCH','/rest/v1/profiles',token=request.cloud_token,params={'id':'eq.'+p['id']},data=data)
    # Header display name stays in sync with the account's existing metadata.
    cloud.api('PUT','/auth/v1/user',token=request.cloud_token,data={'data':{'display_name':name,'avatar_data':data.get('avatar_data',p['avatar_data'])}})

@require_http_methods(['GET','POST'])
def profile(request, user_id=None):
    uid=str(user_id) if user_id else (request.cloud_user or {}).get('id')
    owner=bool(request.cloud_user and uid==request.cloud_user['id'])
    if not uid:
        return render(request,'profile_guest.html',{'active':'profile','auth_ready':cloud.configured(),'cloud_error':request.cloud_error})
    tab=request.GET.get('tab','overview')
    allowed=[t[0] for t in TABS]+(['settings','people','following','followers'] if owner else ['following','followers'])
    private_tabs = ('friends', 'history')
    if tab not in allowed or (tab in private_tabs and not owner): tab='overview'
    ctx={'active':'profile','owner':owner,'tab':tab,'tabs':[t for t in TABS if owner or t[0] not in private_tabs]}
    try:
        if owner: p=own_profile(request)
        else:
            rows=query(request,'profiles',id='eq.'+uid,is_public='eq.true')
            if not rows: raise Http404()
            p=rows[0]
        ctx['person']=p
        from .billing import member
        ctx['supporter']=member(uid)
        if request.method=='POST':
            if not owner: raise Http404()
            try:
                action=request.POST.get('action','profile')
                if action=='password':
                    pw=request.POST.get('password','')
                    validate_password(pw,request.POST.get('password_confirm'))
                    try:
                        cloud.api('PUT','/auth/v1/user',token=request.cloud_token,data={'password':pw})
                    except cloud.CloudError as exc:
                        from .accounts import message
                        raise ValidationError(message(exc)) from exc
                elif action=='profile': edit_profile(request,p)
                elif action=='review':
                    mid=request.POST.get('movie_id','')
                    if len(mid)>100 or not get_provider().get(mid): raise ValidationError('Выберите фильм из каталога.')
                    body=request.POST.get('body','').strip()
                    if not 20<=len(body)<=5000: raise ValidationError('Рецензия должна содержать от 20 до 5000 символов.')
                    cloud.api('POST','/rest/v1/reviews',token=request.cloud_token,
                        params={'on_conflict':'user_id,movie_id'},prefer='resolution=merge-duplicates',
                        data={'user_id':uid,'movie_id':mid,'body':body,'spoiler':request.POST.get('spoiler')=='on','updated_at':datetime.now(timezone.utc).isoformat()})
                elif action=='delete_review':
                    rid=str(UUID(request.POST.get('review_id','')))
                    cloud.api('DELETE','/rest/v1/reviews',token=request.cloud_token,params={'id':'eq.'+rid,'user_id':'eq.'+uid})
                elif action=='delete_comment':
                    cid=str(UUID(request.POST.get('comment_id','')))
                    cloud.api('DELETE','/rest/v1/review_comments',token=request.cloud_token,
                        params={'id':'eq.'+cid,'user_id':'eq.'+uid})
                elif action=='stars':
                    stars=request.POST.getlist('stars')
                    valid={a for m in LocalProvider().search() for a in m.get('cast',[])}
                    if len(stars)>24 or not set(stars)<=valid: raise ValidationError('Выберите до 24 актёров из списка.')
                    cloud.api('PATCH','/rest/v1/profiles',token=request.cloud_token,params={'id':'eq.'+uid},data={'stars':stars})
                else: raise ValidationError('Неизвестное действие.')
                return redirect('/profile/?'+urlencode({'tab':tab,'done':'1'}))
            except (ValidationError,ValueError) as exc:
                ctx['error']=' '.join(exc.messages) if isinstance(exc,ValidationError) else 'Проверьте данные формы.'
                ctx['draft']=request.POST
        if request.GET.get('done')=='1': ctx['success']='Изменения сохранены.'
        rows=query(request,'library',user_id='eq.'+uid,order='updated_at.desc',limit='500')
        ctx.update(saved_count=sum(r['saved'] for r in rows),watched_count=sum(r['watched'] for r in rows),rating_count=sum(r['rating'] is not None for r in rows))
        following=query(request,'follows',follower_id='eq.'+uid,limit='1000')
        followers=query(request,'follows',following_id='eq.'+uid,limit='1000')
        ctx.update(following_count=len(following),followers_count=len(followers))
        ctx['is_following']=bool(request.cloud_user and any(f['follower_id']==request.cloud_user['id'] for f in followers))
        ctx['cover']=LocalProvider().get(p['cover_id']) or LocalProvider().get('interstellar')
        ctx['profile_url']='/people/'+uid+'/'
        if owner and tab in ('overview', 'history'):
            from .watch_history import entries
            try:
                ctx['watch_entries'] = entries(request, limit=3 if tab == 'overview' else 30)
            except cloud.CloudError:
                ctx['watch_history_error'] = True
        if tab=='settings': ctx['covers']=[m for mid in COVERS if (m:=LocalProvider().get(mid))]
        elif tab in ('overview','saved','watched','ratings'):
            selected=[r for r in rows if (r['saved'] if tab=='saved' else r['watched'] if tab=='watched' else r['rating'] is not None if tab=='ratings' else r['watched'] or r['rating'] is not None)]
            ctx['movies']=movie_rows(selected[:12] if tab=='overview' else selected)
            if tab=='overview': ctx['saved_movies']=movie_rows([r for r in rows if r['saved']][:6])
        elif tab=='reviews':
            ctx['reviews']=enrich_reviews(request,query(request,'reviews',user_id='eq.'+uid,order='updated_at.desc',limit='100'))
            if owner:
                ctx['film_choices']=sorted(LocalProvider().search(),key=lambda m:m['title'])
                edit=request.GET.get('edit','')
                ctx['edit_review']=next((r for r in ctx['reviews'] if r['id']==edit),None)
                ctx['selected_movie']=request.GET.get('movie','')
        elif tab=='comments':
            comments=query(request,'review_comments',user_id='eq.'+uid,order='created_at.desc',limit='100')
            for c in comments:
                c['review']=next(iter(enrich_reviews(request,query(request,'reviews',id='eq.'+c['review_id']))),None)
            ctx['comments']=comments
        elif tab=='stars':
            ctx['stars']=p['stars']
            if owner: ctx['star_choices']=sorted({a for m in LocalProvider().search() for a in m.get('cast',[])})
        elif tab=='friends':
            ids=[f['following_id'] for f in following]
            ratings=query(request,'library',user_id='in.('+','.join(ids)+')',rating='not.is.null',order='updated_at.desc',limit='50') if ids else []
            people={p['id']:p for p in query(request,'profiles',id='in.('+','.join(ids)+')')} if ids else {}
            ctx['friend_ratings']=[{'film':get_provider().get(r['movie_id']),'rating':r['rating'],'author':people.get(r['user_id'])} for r in ratings if people.get(r['user_id'])]
        elif tab in ('people','following','followers'):
            params={'is_public':'eq.true','order':'display_name.asc','limit':'48'}
            if tab=='people':
                q=re.sub(r'[^\w\s\-]','',request.GET.get('q',''))[:60].strip();ctx['q']=q
                params['id']='neq.'+uid
                if q: params['display_name']='ilike.*'+q+'*'
            else:
                ids=[f['following_id'] for f in following] if tab=='following' else [f['follower_id'] for f in followers]
                params['id']='in.('+','.join(ids)+')' if ids else 'eq.00000000-0000-0000-0000-000000000000'
            ctx['people']=query(request,'profiles',**params)
            ctx['people_title']={'people':'Найти киноманов','following':'Подписки на людей','followers':'Подписчики'}[tab]
        return render(request,'profile.html',ctx)
    except (cloud.CloudError,ProviderError):
        ctx['error']='Не удалось загрузить или сохранить профиль. Обновите страницу и повторите попытку.'
        return render(request,'profile.html',ctx,status=503)

@require_POST
def follow(request,user_id):
    if not request.cloud_user: return redirect('login')
    uid=request.cloud_user['id'];target=str(user_id)
    if uid==target: return redirect('profile')
    try:
        own_profile(request)
        params={'follower_id':'eq.'+uid,'following_id':'eq.'+target}
        if request.POST.get('action')=='unfollow':
            cloud.api('DELETE','/rest/v1/follows',token=request.cloud_token,params=params)
        else:
            if not query(request,'profiles',id='eq.'+target,is_public='eq.true'): raise Http404()
            cloud.api('POST','/rest/v1/follows',token=request.cloud_token,data={'follower_id':uid,'following_id':target},prefer='resolution=ignore-duplicates')
        return redirect('/people/'+target+'/')
    except cloud.CloudError:
        return render(request,'error.html',{'message':'Не удалось изменить подписку. Попробуйте ещё раз.'},status=503)

@require_http_methods(['GET','POST'])
def review(request,review_id):
    rid=str(review_id);ctx={'active':'profile'}
    try:
        rows=enrich_reviews(request,query(request,'reviews',id='eq.'+rid))
        if not rows: raise Http404()
        r=rows[0];ctx['review']=r
        if request.method=='POST':
            if not request.cloud_user: return redirect('login')
            p=own_profile(request)
            try:
                if request.POST.get('action')=='delete_comment':
                    cid=str(UUID(request.POST.get('comment_id','')))
                    cloud.api('DELETE','/rest/v1/review_comments',token=request.cloud_token,params={'id':'eq.'+cid,'review_id':'eq.'+rid,'user_id':'eq.'+p['id']})
                else:
                    body=request.POST.get('body','').strip()
                    if not p['is_public']: raise ValidationError('Откройте профиль в настройках, чтобы участвовать в обсуждении.')
                    if not 1<=len(body)<=1000: raise ValidationError('Комментарий должен содержать от 1 до 1000 символов.')
                    cloud.api('POST','/rest/v1/review_comments',token=request.cloud_token,data={'user_id':p['id'],'review_id':rid,'body':body})
                return redirect('/reviews/'+rid+'/')
            except (ValidationError,ValueError) as exc:
                ctx['error']=' '.join(exc.messages) if isinstance(exc,ValidationError) else 'Проверьте данные.'
        comments=query(request,'review_comments',review_id='eq.'+rid,order='created_at.asc',limit='100')
        ids={c['user_id'] for c in comments}
        people={p['id']:p for p in query(request,'profiles',id='in.('+','.join(ids)+')')} if ids else {}
        for c in comments: c['author']=people.get(c['user_id'],{'display_name':'Киноман'})
        ctx['comments']=comments
        return render(request,'review.html',ctx)
    except (cloud.CloudError,ProviderError):
        return render(request,'error.html',{'message':'Обсуждение временно недоступно. Попробуйте позже.'},status=503)
