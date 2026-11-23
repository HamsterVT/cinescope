"""WhatsApp verification is delivered and checked by Supabase, never locally."""
import re
import time
from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.middleware.csrf import rotate_token
from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods
from . import cloud
from .accounts import message
from .passwords import validate_password

def normalize_phone(value):
    value=value.strip()
    if len(value)>32 or re.search(r'[^0-9+()\s-]',value):
        raise ValidationError('Введите номер в международном формате, например +7 701 123 45 67.')
    digits=re.sub(r'\D','',value)
    if len(digits)==11 and digits.startswith('8'): digits='7'+digits[1:]
    if not re.fullmatch(r'[1-9][0-9]{7,14}',digits):
        raise ValidationError('Введите номер с кодом страны, например +7 701 123 45 67.')
    return '+'+digits

def pending(request):
    try:
        return signing.loads(request.COOKIES.get('cs_phone',''),salt='phone-otp',max_age=600)
    except signing.BadSignature: return None

def remember(response,number,mode):
    response.set_cookie('cs_phone',signing.dumps({'phone':number,'mode':mode,'sent':time.time()},salt='phone-otp'),
        max_age=600,httponly=True,secure=not settings.DEBUG,samesite='Lax')
    return response

@require_http_methods(['GET','POST'])
def account(request):
    if request.cloud_user: return redirect('profile')
    mode=request.GET.get('mode','login')
    if mode not in ('signup','login','recover'): mode='login'
    state=pending(request)
    if state and state['mode']!=mode: state=None
    ctx={'active':'profile','mode':mode,'phone_ready':settings.AUTH_PHONE_READY,
         'auth_ready':settings.AUTH_PHONE_READY and cloud.configured(),
         'stage':'verify' if state else 'number','phone':state['phone'] if state else ''}
    if request.method=='POST':
        if not ctx['auth_ready']:
            ctx['error']='Подтверждение через WhatsApp ещё подключается. Пока используйте email.'
            return render(request,'phone.html',ctx,status=503)
        try:
            action=request.POST.get('action','send')
            if action=='change':
                response=redirect('/account/phone/?mode='+mode)
                response.delete_cookie('cs_phone')
                return response
            if action=='verify':
                if not state: raise ValidationError('Время подтверждения истекло. Запросите новый код.')
                code=request.POST.get('code','').strip()
                if not re.fullmatch(r'[0-9]{6}',code): raise ValidationError('Введите 6 цифр из сообщения WhatsApp.')
                result=cloud.api('POST','/auth/v1/verify',data={'phone':state['phone'],'token':code,'type':'sms'})
                rotate_token(request)
                response=cloud.set_tokens(redirect('/profile/?tab=settings' if mode=='recover' else '/profile/'),result)
                response.delete_cookie('cs_phone')
                return response
            if action not in ('send','resend'): raise ValidationError('Неизвестное действие.')
            if state and time.time()-state['sent']<60:
                raise ValidationError('Код уже отправлен. Повторить отправку можно через минуту.')
            number=state['phone'] if action=='resend' and state else normalize_phone(request.POST.get('phone',''))
            ctx['phone']=number
            if mode=='signup' and action=='send':
                password=request.POST.get('password','')
                validate_password(password)
                cloud.api('POST','/auth/v1/signup',data={'phone':number,'password':password,'channel':'whatsapp',
                    'data':{'display_name':request.POST.get('display_name','').strip()[:60] or 'Киноман'}})
            else:
                cloud.api('POST','/auth/v1/otp',data={'phone':number,'channel':'whatsapp','create_user':False})
            return remember(redirect('/account/phone/?mode='+mode),number,mode)
        except ValidationError as exc: ctx['error']=' '.join(exc.messages)
        except cloud.CloudError as exc: ctx['error']=message(exc)
    return render(request,'phone.html',ctx)
