"""Server-priced, account-bound Plus orders; BTC settlement is authoritative."""
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4
from urllib.parse import urlencode, urlsplit

import httpx
from django.conf import settings
from django.core import signing
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_GET

from . import cloud, payments

PLANS = {'monthly': {'title': 'Месяц с Plus', 'amount': '1990', 'display': '1 990', 'period': '1 месяц'},
         'annual': {'title': 'Год с Plus', 'amount': '15990', 'display': '15 990', 'period': '12 месяцев'}}
TERMS_VERSION = 'supporter-2026-11-09'
RECOVERY_WINDOW = timedelta(minutes=2)
logger = logging.getLogger(__name__)

STATUS_LABELS = {
    'creating': 'Счёт готовится', 'awaiting': 'Ожидает оплаты',
    'partial': 'Частично оплачен', 'processing': 'Подтверждается',
    'paid': 'Оплачено', 'review': 'Требует проверки', 'expired': 'Счёт истёк',
    'failed': 'Счёт не создан',
}


def api(method, path, *, data=None, params=None, prefer=None):
    if not settings.SUPABASE_BILLING_SECRET_KEY:
        raise payments.PaymentError('billing_not_configured')
    headers = {'apikey': settings.SUPABASE_BILLING_SECRET_KEY}
    if prefer:
        headers['Prefer'] = prefer
    try:
        r = httpx.request(method, payments.secure_origin(settings.SUPABASE_URL) + '/rest/v1/' + path,
                          json=data, params=params, headers=headers, follow_redirects=False,
                          timeout=httpx.Timeout(12, connect=3))
        if not r.is_success:
            raise payments.PaymentError('billing_unavailable')
        return r.json() if r.content else None
    except (httpx.HTTPError, ValueError) as exc:
        raise payments.PaymentError('billing_unavailable') from exc


def member(user_id):
    if not settings.SUPABASE_BILLING_SECRET_KEY:
        return None
    try:
        rows = api('GET', 'billing_memberships', params={'user_id': 'eq.' + str(UUID(str(user_id))), 'limit': '1'}) or []
        row = rows[0] if rows else None
        if row:
            row['until'] = datetime.fromisoformat(row['valid_until'].replace('Z', '+00:00'))
            row['active'] = not row['blocked'] and row['until'] > datetime.now(timezone.utc)
        return row
    except (payments.PaymentError, ValueError, KeyError):
        return None


def owned_order(request, order_id):
    # Read via the user's JWT AND owner filter. RLS is a second independent guard.
    if not request.cloud_user:
        raise Http404
    try:
        rows = cloud.api('GET', '/rest/v1/billing_orders', token=request.cloud_token,
                         params={'id': 'eq.' + str(order_id), 'user_id': 'eq.' + request.cloud_user['id'], 'limit': '1'}) or []
    except cloud.CloudError as exc:
        raise payments.PaymentError('billing_unavailable') from exc
    if not rows:
        raise Http404
    return rows[0]


def reconcile(current):
    """Never infer order ownership/price from browser input or webhook metadata alone."""
    try:
        order_id = str(UUID(str(current.get('metadata', {}).get('orderId', ''))))
    except (ValueError, TypeError, AttributeError):
        return None  # An invoice made outside CineScope's order flow.
    if current.get('storeId') != settings.BTCPAY_STORE_ID:
        raise payments.PaymentError('wrong_store')
    return api('POST', 'rpc/billing_reconcile', data={
        'p_order': order_id, 'p_invoice': payments.identifier(current.get('id')),
        'p_store': settings.BTCPAY_STORE_ID, 'p_amount': payments.decimal_amount(current.get('amount')),
        'p_currency': current.get('currency'), 'p_status': current.get('status'),
        'p_additional': current.get('additionalStatus') or 'None',
        'p_paid': payments.decimal_amount(current.get('paidAmount')),
    })


def checkout_url(current):
    value = current.get('checkoutLink', '')
    try:
        p = urlsplit(value)
        expected = urlsplit(payments.secure_origin(settings.BTCPAY_URL))
        invoice_id = payments.identifier(current.get('id'))
        if (p.scheme == 'https' and p.netloc == expected.netloc and p.path == '/i/' + invoice_id
                and not p.username and not p.password and not p.fragment):
            return value
    except (ValueError, TypeError):
        pass
    raise payments.PaymentError('invalid_checkout_url')


def order_invoice(order):
    if order.get('invoice_id'):
        return payments.invoice(order['invoice_id'])
    # Recover a timed-out invoice creation without creating a second invoice.
    rows = payments.provider_request('GET', payments.store_path('/invoices') + '?' +
                                    urlencode({'orderId': order['id'], 'take': '2'}))
    if not isinstance(rows, list) or len(rows) != 1:
        return None
    result = rows[0]
    if result.get('metadata', {}).get('orderId') != order['id']:
        raise payments.PaymentError('invoice_mismatch')
    return payments.invoice(result['id'])


def invoice_state(order):
    status = order['status']
    additional = order.get('additional_status')
    if order.get('needs_review') or additional in ('Marked', 'Invalid'):
        return 'review'
    if order.get('fulfilled_at'):
        return 'paid'
    if additional == 'PaidPartial':
        return 'partial'
    if status in ('Invalid', 'Settled') or additional == 'PaidLate' and status == 'Expired':
        return 'review'
    return {'New': 'awaiting', 'Processing': 'processing', 'Expired': 'expired',
            'Creating': 'creating', 'Error': 'failed'}[status]


def present_order(order):
    order['state'] = invoice_state(order)
    order['status_label'] = STATUS_LABELS[order['state']]
    order['can_open_payment'] = order['status'] == 'New' and order['state'] in ('awaiting', 'partial')
    order['can_refresh'] = order['state'] not in ('paid', 'failed')
    if order.get('access_until'):
        order['until'] = datetime.fromisoformat(order['access_until'].replace('Z', '+00:00'))
    if order.get('created_at'):
        order['created'] = datetime.fromisoformat(order['created_at'].replace('Z', '+00:00'))
        order['creation_delayed'] = datetime.now(timezone.utc) - order['created'] >= RECOVERY_WINDOW
        if order['state'] == 'creating' and order['creation_delayed']:
            order['status_label'] = 'Проверьте счёт'
    return order


@never_cache
@require_http_methods(['GET', 'POST'])
def checkout(request):
    key = request.POST.get('plan') if request.method == 'POST' else request.GET.get('plan', 'monthly')
    if key not in PLANS:
        raise Http404
    ctx = {'active': 'subscription', 'plan_key': key, 'plan': PLANS[key],
           'membership': member(request.cloud_user['id']) if request.cloud_user else None}
    if request.method == 'POST':
        if not request.cloud_user:
            return redirect('/account/login/')
        if request.POST.get('consent') != TERMS_VERSION:
            ctx['error'] = 'Подтвердите условия Plus перед переходом к оплате.'
        else:
            try:
                signed = signing.loads(request.POST.get('checkout_token', ''), salt='plus-checkout', max_age=1800)
                if signed['user'] != request.cloud_user['id'] or signed['plan'] != key:
                    raise signing.BadSignature
                order_id = str(UUID(signed['order']))
            except (signing.BadSignature, KeyError, ValueError, TypeError):
                ctx['error'] = 'Страница оформления устарела. Проверьте тариф и повторите оплату.'
            else:
                reserved = False
                try:
                    if not any(m.get('enabled') is True and m.get('paymentMethodId') == payments.BTC_ONCHAIN
                               for m in payments.payment_methods()):
                        raise payments.PaymentError('wallet_unavailable')
                    result = api('POST', 'rpc/billing_reserve', data={'p_id': order_id,
                                 'p_user': request.cloud_user['id'], 'p_plan': key, 'p_store': settings.BTCPAY_STORE_ID})
                    reserved = True
                    if not result['created']:
                        return redirect('billing_order', order_id=order_id)
                    current = payments.provider_request('POST', payments.store_path('/invoices'), data={
                        'amount': PLANS[key]['amount'], 'currency': 'KZT',
                        'metadata': {'orderId': order_id, 'itemDesc': 'CineScope Plus — поддержка проекта, ' + PLANS[key]['period']},
                        'checkout': {'expirationMinutes': 15, 'paymentTolerance': 0, 'defaultLanguage': 'ru',
                                     'paymentMethods': [payments.BTC_ONCHAIN], 'lazyPaymentMethods': False,
                                     'redirectURL': settings.SITE_URL.rstrip('/') + reverse('billing_order', args=[order_id]),
                                     'redirectAutomatically': False},
                    })
                    saved = reconcile(current)
                    if not saved or saved['id'] != order_id:
                        raise payments.PaymentError('invoice_mismatch')
                    return redirect(checkout_url(current))
                except payments.InvoiceRejected:
                    try:
                        api('PATCH', 'billing_orders', data={'status': 'Error'}, params={
                            'id': 'eq.' + order_id, 'user_id': 'eq.' + request.cloud_user['id'],
                            'status': 'eq.Creating', 'invoice_id': 'is.null',
                        })
                    except payments.PaymentError:
                        logger.warning('Could not record rejected BTCPay invoice')
                    return redirect('billing_order', order_id=order_id)
                except (payments.PaymentError, KeyError, TypeError, ValueError) as exc:
                    # Keep the reserved order for recovery if BTCPay accepted a request before a timeout.
                    logger.warning('Plus checkout incomplete (%s)', type(exc).__name__)
                    if reserved:
                        return redirect('billing_order', order_id=order_id)
                    ctx['error'] = 'Не удалось создать счёт. Попробуйте позже.'
    ctx['terms_version'] = TERMS_VERSION
    ctx['checkout_token'] = signing.dumps({'user': (request.cloud_user or {}).get('id'), 'plan': key, 'order': locals().get('order_id') or str(uuid4())}, salt='plus-checkout')
    response = render(request, 'checkout.html', ctx)
    if not request.cloud_user:
        response.set_signed_cookie('cs_checkout', key, salt='checkout-return', max_age=3600,
                                   httponly=True, secure=not settings.DEBUG, samesite='Lax')
    return response


@never_cache
@require_http_methods(['GET', 'POST'])
def order_page(request, order_id):
    status_request = request.method == 'GET' and request.GET.get('status') == '1'
    if not request.cloud_user:
        if status_request:
            return JsonResponse({'error': 'sign_in_required'}, status=401)
        return redirect('/account/login/')
    try:
        order = owned_order(request, order_id)
    except payments.PaymentError:
        if status_request:
            return JsonResponse({'error': 'temporarily_unavailable'}, status=503)
        return render(request, 'error.html', {'message': 'Не удалось загрузить заказ. Обновите страницу через минуту.'}, status=503)
    if status_request:
        # Read the webhook-updated state without repeatedly calling the payment provider.
        return JsonResponse({'state': invoice_state(order)})
    error = ''
    if request.method == 'POST':
        try:
            current = order_invoice(order)
            if current:
                # An invoice cannot be attached to a different order even if its price matches.
                if current.get('metadata', {}).get('orderId') != str(order_id):
                    raise payments.PaymentError('wrong_order')
                order = reconcile(current) or order
                if (request.POST.get('action') == 'pay' and order['status'] == 'New'
                        and invoice_state(order) in ('awaiting', 'partial')):
                    return redirect(checkout_url(current))
                return redirect('billing_order', order_id=order_id)
            else:
                error = 'BTCPay пока не нашёл счёт для этого заказа.'
        except (payments.PaymentError, KeyError, TypeError, ValueError):
            error = 'Проверка оплаты временно недоступна. Повторите её через минуту; повторно платить не нужно.'
    return render(request, 'billing_order.html', {'active': 'subscription', 'order': present_order(order),
                  'plan': PLANS[order['plan']], 'error': error})


@never_cache
@require_GET
def history(request):
    if not request.cloud_user:
        return redirect('/account/login/')
    error, rows = '', []
    try:
        rows = cloud.api('GET', '/rest/v1/billing_orders', token=request.cloud_token,
                         params={'user_id': 'eq.' + request.cloud_user['id'], 'order': 'created_at.desc', 'limit': '50'}) or []
        for row in rows:
            row['title'] = PLANS[row['plan']]['title']
            present_order(row)
    except (cloud.CloudError, ValueError, KeyError):
        error = 'История временно недоступна. Попробуйте позже.'
    return render(request, 'billing_history.html', {'active': 'subscription', 'orders': rows, 'error': error,
                  'membership': member(request.cloud_user['id'])})
