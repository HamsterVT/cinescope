import base64
import hashlib
import json
import secrets
from datetime import datetime, timezone
from django.conf import settings
from django.core import signing
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.middleware.csrf import rotate_token
from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods, require_POST, require_GET
from . import cloud
from .providers import get_provider, ProviderError
from .passwords import validate_password, HELP

MESSAGES = {'invalid_credentials':'Неверный email или пароль.',
 'email_not_confirmed':'Подтвердите email по ссылке из письма.',
 'over_email_send_rate_limit':'Лимит писем исчерпан. Попробуйте позже.',
 'over_request_rate_limit':'Слишком много попыток. Подождите немного.',
 'email_address_not_authorized':'Отправка писем ещё настраивается. Регистрация временно недоступна.',
 'not_configured':'Облачные аккаунты подключаются. Попробуйте позже.',
 'weak_password':HELP,
 'reauthentication_needed':'Для смены пароля выйдите из аккаунта и войдите снова, затем повторите попытку.',
 'user_already_exists':'Этот аккаунт уже существует. Попробуйте войти.',
 'signup_disabled':'Регистрация временно недоступна.',
 'otp_expired':'Код неверный или устарел. Проверьте его или запросите новый.',
 'sms_send_failed':'Не удалось отправить код в WhatsApp. Попробуйте позже.',
 'over_sms_send_rate_limit':'Код уже отправлен. Подождите минуту перед повторным запросом.',
 'phone_exists':'Номер уже зарегистрирован. Попробуйте войти.'}

def message(error):
    return MESSAGES.get(error.code, 'Не удалось связаться с сервисом. Попробуйте позже.')

def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    return verifier, challenge

def pkce_cookie(response, verifier, mode):
    response.set_cookie('cs_pkce', signing.dumps({'v':verifier,'mode':mode},salt='pkce'),
                        max_age=3600, httponly=True, secure=not settings.DEBUG, samesite='Lax')
    return response

@require_http_methods(['GET','POST'])
def account(request, mode='login'):
    if request.cloud_user and mode in ('login','signup'): return redirect('profile')
    if request.method=='GET' and settings.AUTH_PHONE_READY and not settings.AUTH_TELEGRAM_READY and request.GET.get('method')!='email':
        return redirect('/account/phone/?mode='+mode)
    ctx = {'mode':mode,'active':'profile','phone_ready':settings.AUTH_PHONE_READY,'auth_ready':cloud.configured() and (mode == 'login' or settings.AUTH_EMAIL_READY)}
    if request.method == 'POST' and not ctx['auth_ready']:
        ctx['error']='Регистрация и восстановление по email временно недоступны: настраиваем отправку писем.'
        return render(request,'account.html',ctx,status=503)
    if request.method == 'POST':
        email = request.POST.get('email','').strip()[:254]
        password = request.POST.get('password','')
        ctx['email'] = email
        try:
            validate_email(email)
            if mode == 'login' and not (1 <= len(password) <= 128):
                raise ValidationError('Введите пароль.')
            if mode == 'login':
                result = cloud.api('POST','/auth/v1/token',params={'grant_type':'password'},data={'email':email,'password':password})
                rotate_token(request)
                return cloud.set_tokens(redirect('profile'),result)
            verifier, challenge = pkce()
            data = {'email':email,'code_challenge':challenge,'code_challenge_method':'s256'}
            if mode == 'signup':
                validate_password(password,request.POST.get('password_confirm'))
                name = request.POST.get('display_name','').strip()[:60]
                data.update(password=password,data={'display_name':name or 'Киноман'})
            result = cloud.api('POST','/auth/v1/signup' if mode == 'signup' else '/auth/v1/recover',
                params={'redirect_to':settings.SITE_URL+'/account/callback/'},data=data)
            if mode == 'signup' and result and result.get('access_token'):
                rotate_token(request)
                return cloud.set_tokens(redirect('profile'),result)
            ctx['success'] = 'Если адрес доступен для этого действия, мы отправили письмо. Откройте ссылку в этом же браузере.'
            return pkce_cookie(render(request,'account.html',ctx),verifier,mode)
        except ValidationError as exc: ctx['error'] = ' '.join(exc.messages)
        except cloud.CloudError as exc: ctx['error'] = message(exc)
    return render(request,'account.html',ctx)

@require_GET
def callback(request):
    try:
        state = signing.loads(request.COOKIES.get('cs_pkce',''),salt='pkce',max_age=3600)
        if state.get('mode') in ('telegram_login', 'telegram_link'):
            from .telegram import finish
            return finish(request)
        if state.get('mode') not in ('signup', 'recover'):
            raise ValueError()
        code = request.GET.get('code','')
        if not code or len(code)>2048: raise ValueError()
        result = cloud.api('POST','/auth/v1/token',params={'grant_type':'pkce'},data={'auth_code':code,'code_verifier':state['v']})
        rotate_token(request)
        response = cloud.set_tokens(redirect('/profile/?tab=settings' if state['mode']=='recover' else '/profile/'),result)
        response.delete_cookie('cs_pkce')
        return response
    except (signing.BadSignature, ValueError, KeyError, AttributeError, cloud.CloudError):
        response=render(request,'account.html',{'mode':'login','error':'Ссылка устарела или открыта в другом браузере. Начните вход заново.','auth_ready':cloud.configured()},status=400)
        response.delete_cookie('cs_pkce',path='/',samesite='Lax')
        return response

@require_POST
def logout(request):
    if request.cloud_token:
        try: cloud.api('POST','/auth/v1/logout',token=request.cloud_token,params={'scope':'local'})
        except cloud.CloudError:
            return render(request,'error.html',{'message':'Не удалось завершить облачный сеанс. Повторите выход через минуту.'},status=503)
    rotate_token(request)
    return cloud.clear_tokens(redirect('/'))

@require_http_methods(['GET','POST'])
def profile(request):
    if not request.cloud_user:
        return render(request,'profile_guest.html',{'active':'profile','auth_ready':cloud.configured(),'cloud_error':request.cloud_error})
    tab = request.GET.get('tab','saved')
    if tab not in ('saved','watched','ratings','settings'): tab='saved'
    ctx = {'active':'profile','tab':tab}
    if request.method == 'POST':
        try:
            if request.POST.get('action') == 'password':
                password=request.POST.get('password','')
                validate_password(password,request.POST.get('password_confirm'))
                cloud.api('PUT','/auth/v1/user',token=request.cloud_token,data={'password':password})
            else:
                name=request.POST.get('display_name','').strip()
                if not 1<=len(name)<=60: raise ValidationError('Имя должно содержать от 1 до 60 символов.')
                user=cloud.api('PUT','/auth/v1/user',token=request.cloud_token,data={'data':{'display_name':name}})
                request.cloud_user=user
            ctx['success']='Изменения сохранены.'
        except cloud.CloudError as exc: ctx['error']=message(exc)
        except ValidationError as exc: ctx['error']=' '.join(exc.messages)
    try: rows=cloud.library(request)
    except cloud.CloudError:
        rows=[];ctx['error']='Не удалось загрузить облачную библиотеку. Ваши данные не потеряны — обновите страницу позже.'
    ctx.update(saved_count=sum(r['saved'] for r in rows),watched_count=sum(r['watched'] for r in rows),rating_count=sum(r['rating'] is not None for r in rows))
    selected=[r for r in rows if (r['saved'] if tab=='saved' else r['watched'] if tab=='watched' else r['rating'] is not None if tab=='ratings' else False)]
    ctx['movies']=[]
    for row in selected:
        try: movie=get_provider().get(row['movie_id'])
        except ProviderError: movie=None
        if movie: ctx['movies'].append({**movie,'personal_rating':row['rating']})
    return render(request,'profile.html',ctx)

@require_http_methods(['GET','POST'])
def library_api(request):
    if not request.cloud_user:
        return JsonResponse({'error':'Войдите, чтобы сохранить в облако.'},status=503 if request.cloud_error else 401)
    try:
        if request.method=='GET': return JsonResponse({'items':cloud.library(request)})
        if len(request.body)>4096: return JsonResponse({'error':'Слишком большой запрос.'},status=400)
        data=json.loads(request.body)
        if not isinstance(data,dict): raise ValueError()
        mid=data.get('movie_id','')
        if not isinstance(mid,str) or len(mid)>100: raise ValueError()
        movie=get_provider().get(mid)
        if not movie: return JsonResponse({'error':'Фильм не найден.'},status=404)
        row={'user_id':request.cloud_user['id'],'movie_id':mid,'movie':movie,'updated_at':datetime.now(timezone.utc).isoformat()}
        for field in ['saved','watched']:
            if field in data:
                if type(data[field]) is not bool: raise ValueError()
                row[field]=data[field]
        if 'rating' in data:
            rating=data['rating']
            if rating is not None and (type(rating) is not int or not 1<=rating<=10): raise ValueError()
            row['rating']=rating
        if not any(k in row for k in ('saved','watched','rating')): raise ValueError()
        # PATCH an existing row preserves other actions. POST only creates absent rows;
        # conflict-ignore handles concurrent first writes, then PATCH applies this action.
        params={'user_id':'eq.'+row['user_id'],'movie_id':'eq.'+mid}
        cloud.api('POST','/rest/v1/library',token=request.cloud_token,data=row,
                  params={'on_conflict':'user_id,movie_id'},prefer='resolution=ignore-duplicates,return=minimal')
        result=cloud.api('PATCH','/rest/v1/library',token=request.cloud_token,data=row,params=params,prefer='return=representation')
        return JsonResponse({'item':result[0]})
    except (ValueError,TypeError,KeyError,IndexError): return JsonResponse({'error':'Некорректные данные.'},status=400)
    except (cloud.CloudError,ProviderError): return JsonResponse({'error':'Сохранение временно недоступно. Повторите попытку.'},status=503)
