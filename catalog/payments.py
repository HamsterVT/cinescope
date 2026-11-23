"""Signed BTCPay receipts and reconciliation with server-owned Plus orders."""
import hashlib
import hmac
import json
import logging
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, urlsplit

import httpx
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

MAX_WEBHOOK_BYTES = 65_536
BTC_ONCHAIN = 'BTC-CHAIN'
logger = logging.getLogger(__name__)
INVOICE_EVENTS = frozenset({
    'InvoiceCreated', 'InvoiceReceivedPayment', 'InvoiceProcessing',
    'InvoiceExpired', 'InvoiceSettled', 'InvoiceInvalid', 'InvoicePaymentSettled',
    'InvoiceExpiredPaidPartial', 'InvoicePaidAfterExpiration',
})
STATUSES = frozenset({'New', 'Processing', 'Expired', 'Invalid', 'Settled'})
ADDITIONAL_STATUSES = frozenset({'None', 'PaidLate', 'PaidPartial', 'Marked', 'Invalid', 'PaidOver'})
IDENTIFIER = re.compile(r'^[A-Za-z0-9_-]{1,128}$')


class PaymentError(Exception):
    """Deliberately exclude upstream bodies, URLs and credentials from errors."""


class InvoiceRejected(PaymentError):
    """BTCPay explicitly rejected creation, so there is no invoice to recover."""


def secure_origin(value):
    try:
        p = urlsplit(value)
        if (p.scheme == 'https' and p.hostname and not p.username and not p.password
                and p.port in (None, 443) and p.path in ('', '/') and not p.query and not p.fragment):
            return 'https://' + p.hostname
    except (ValueError, TypeError):
        pass
    raise PaymentError('invalid_origin')


def identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError('invalid_identifier')
    return value


def provider_request(method, path, *, data=None):
    if not settings.BTCPAY_API_KEY or not settings.BTCPAY_STORE_ID:
        raise PaymentError('not_configured')
    origin = secure_origin(settings.BTCPAY_URL)
    try:
        response = httpx.request(
            method, origin + path,
            headers={'Authorization': 'token ' + settings.BTCPAY_API_KEY},
            json=data, timeout=httpx.Timeout(12, connect=3), follow_redirects=False,
        )
        if not response.is_success:
            logger.warning('BTCPay %s returned HTTP %s', method, response.status_code)
            if method == 'POST' and response.status_code in (400, 401, 403, 404, 422):
                raise InvoiceRejected('invoice_rejected')
            raise PaymentError('provider_unavailable')
        if len(response.content) > 1_048_576:
            raise PaymentError('provider_unavailable')
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning('BTCPay %s failed (%s)', method, type(exc).__name__)
        raise PaymentError('provider_unavailable') from exc


def store_path(suffix=''):
    return '/api/v1/stores/' + quote(identifier(settings.BTCPAY_STORE_ID), safe='') + suffix


def payment_methods():
    result = provider_request('GET', store_path('/payment-methods'))
    if not isinstance(result, list):
        raise PaymentError('invalid_provider_response')
    return result


def invoice(invoice_id):
    # Path verified against this host's v2.4.7 OpenAPI; not the old store-prefixed API.
    result = provider_request('GET', '/api/v1/invoices/' + quote(identifier(invoice_id), safe=''))
    if not isinstance(result, dict):
        raise PaymentError('invalid_provider_response')
    if result.get('id') != invoice_id or result.get('storeId') != settings.BTCPAY_STORE_ID:
        raise PaymentError('invoice_mismatch')
    return result


def decimal_amount(value):
    try:
        # Bound inputs before Decimal parsing and reject booleans, NaN and infinity.
        if (isinstance(value, bool) or not isinstance(value, (str, int))
                or not re.fullmatch(r'(?:0|[1-9][0-9]{0,18})(?:\.[0-9]{1,18})?', str(value))):
            raise ValueError
        amount = Decimal(value)
        if not amount.is_finite() or amount < 0 or amount > Decimal('1000000000000000000'):
            raise ValueError
        return format(amount, 'f')
    except (ValueError, InvalidOperation):
        raise PaymentError('invalid_amount') from None


def persist_receipt(event, raw):
    """Reconcile from the authenticated API and persist only minimal financial data.

    No payer address, email, IP, raw event, or user-controlled metadata is retained.
    Database uniqueness handles repeated/concurrent deliveries across Vercel workers.
    """
    current = invoice(event['invoiceId'])
    status = current.get('status')
    additional = current.get('additionalStatus') or 'None'
    currency = current.get('currency')
    if (status not in STATUSES or additional not in ADDITIONAL_STATUSES
            or not isinstance(currency, str) or not re.fullmatch(r'[A-Z0-9]{2,16}', currency)):
        raise PaymentError('invalid_invoice')
    receipt = {
        'store_id': settings.BTCPAY_STORE_ID,
        'delivery_id': event['deliveryId'],
        'original_delivery_id': event.get('originalDeliveryId') or event['deliveryId'],
        'invoice_id': event['invoiceId'], 'event_type': event['type'],
        'invoice_status': status, 'additional_status': additional,
        'amount': decimal_amount(current.get('amount')),
        'paid_amount': decimal_amount(current.get('paidAmount')),
        'currency': currency,
        'body_sha256': hashlib.sha256(raw).hexdigest(),
    }
    if not settings.SUPABASE_BILLING_SECRET_KEY:
        raise PaymentError('inbox_not_configured')
    try:
        response = httpx.post(
            secure_origin(settings.SUPABASE_URL) + '/rest/v1/billing_receipts',
            # A separate server-only API key; never mix this into cloud.api/user requests.
            headers={'apikey': settings.SUPABASE_BILLING_SECRET_KEY,
                     'Prefer': 'resolution=ignore-duplicates,return=minimal'},
            params={'on_conflict': 'store_id,delivery_id'}, json=receipt,
            timeout=httpx.Timeout(10, connect=3), follow_redirects=False,
        )
        if not response.is_success:
            raise PaymentError('inbox_unavailable')
    except httpx.HTTPError as exc:
        raise PaymentError('inbox_unavailable') from exc
    from .billing import reconcile
    reconcile(current)


@csrf_exempt  # Provider-to-server POST uses a signed raw body instead of a browser CSRF token.
@never_cache
@require_POST
def webhook(request):
    secret = settings.BTCPAY_WEBHOOK_SECRET
    if not secret or not settings.BTCPAY_WEBHOOK_ID:
        return JsonResponse({'error': 'not_configured'}, status=503)
    if request.content_type != 'application/json':
        return JsonResponse({'error': 'invalid_content_type'}, status=415)
    raw = request.read(MAX_WEBHOOK_BYTES + 1)
    if len(raw) > MAX_WEBHOOK_BYTES:
        return JsonResponse({'error': 'payload_too_large'}, status=413)
    expected = 'sha256=' + hmac.new(secret.encode('utf-8'), raw, hashlib.sha256).hexdigest()
    signature = request.headers.get('BTCPay-Sig', '')
    if not re.fullmatch(r'sha256=[0-9a-f]{64}', signature) or not hmac.compare_digest(expected, signature):
        return JsonResponse({'error': 'invalid_signature'}, status=401)
    try:
        event = json.loads(raw)
        if not isinstance(event, dict):
            raise ValueError
        if event.get('storeId') != settings.BTCPAY_STORE_ID or event.get('webhookId') != settings.BTCPAY_WEBHOOK_ID:
            return JsonResponse({'error': 'wrong_destination'}, status=400)
        if event.get('type') not in INVOICE_EVENTS:
            return JsonResponse({'received': True, 'ignored': True})
        for key in ('invoiceId', 'deliveryId'):
            identifier(event.get(key))
        if event.get('originalDeliveryId'):
            identifier(event['originalDeliveryId'])
    except (ValueError, TypeError, UnicodeDecodeError):
        return JsonResponse({'error': 'invalid_payload'}, status=400)
    try:
        persist_receipt(event, raw)
    except PaymentError as exc:
        # No 2xx until durable storage succeeds. Let BTCPay retry a failed delivery.
        logger.warning('BTCPay webhook reconciliation failed (%s)', exc.args[0])
        return JsonResponse({'error': 'temporarily_unavailable'}, status=503)
    return JsonResponse({'received': True})
