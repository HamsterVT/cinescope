import hashlib
import hmac
import json
from unittest.mock import patch

import httpx
from django.test import Client, SimpleTestCase, override_settings

from . import payments


@override_settings(ALLOWED_HOSTS=['testserver'], BTCPAY_URL='https://pay.example',
                   BTCPAY_STORE_ID='store1', BTCPAY_API_KEY='provider-secret',
                   BTCPAY_WEBHOOK_SECRET='webhook-secret', BTCPAY_WEBHOOK_ID='hook1',
                   SUPABASE_URL='https://database.example',
                   SUPABASE_BILLING_SECRET_KEY='billing-secret')
class PaymentTests(SimpleTestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.event = {'deliveryId': 'delivery1', 'webhookId': 'hook1', 'storeId': 'store1',
                      'invoiceId': 'invoice1', 'type': 'InvoiceSettled',
                      'metadata': {'email': 'do-not-store@example.com'}}
        self.current = {'id': 'invoice1', 'storeId': 'store1', 'status': 'Settled',
                        'additionalStatus': 'None', 'amount': '1990.00',
                        'paidAmount': '1990.00', 'currency': 'KZT'}

    def post(self, event=None, raw=None, signature=None, **kwargs):
        raw = raw if raw is not None else json.dumps(self.event if event is None else event).encode()
        signature = signature or 'sha256=' + hmac.new(b'webhook-secret', raw, hashlib.sha256).hexdigest()
        return self.client.post('/billing/btcpay/webhook/', data=raw, content_type='application/json',
                                HTTP_BTCPAY_SIG=signature, **kwargs)

    def test_signed_delivery_is_verified_and_durable_without_granting_access(self):
        with patch('catalog.payments.provider_request', return_value=self.current) as provider, \
                patch('catalog.payments.httpx.post', return_value=httpx.Response(201)) as database:
            response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])
        provider.assert_called_once_with('GET', '/api/v1/invoices/invoice1')
        sent = database.call_args.kwargs
        self.assertEqual(sent['json']['amount'], '1990.00')
        self.assertNotIn('metadata', sent['json'])
        self.assertNotIn('email', json.dumps(sent['json']))
        self.assertEqual(sent['headers']['apikey'], 'billing-secret')
        self.assertNotIn('provider-secret', repr(sent))
        self.assertIn('ignore-duplicates', sent['headers']['Prefer'])
        self.assertEqual(sent['params']['on_conflict'], 'store_id,delivery_id')
        self.assertEqual(response.json(), {'received': True})

    def test_signature_verifies_exact_raw_bytes_before_any_external_call(self):
        raw = json.dumps(self.event).encode()
        sig = 'sha256=' + hmac.new(b'webhook-secret', raw, hashlib.sha256).hexdigest()
        with patch('catalog.payments.persist_receipt') as persist:
            self.assertEqual(self.post(raw=raw + b' ', signature=sig).status_code, 401)
            self.assertEqual(self.post(signature='sha256=forged').status_code, 401)
            persist.assert_not_called()

    def test_unconfigured_disabled_and_get_cannot_start_payment(self):
        self.assertEqual(self.client.get('/billing/btcpay/webhook/').status_code, 405)
        with override_settings(BTCPAY_WEBHOOK_SECRET=''):
            self.assertEqual(self.post().status_code, 503)

    def test_signed_invalid_payload_and_wrong_destination(self):
        for event in [[], {}, {**self.event, 'storeId': 'another-store'},
                      {**self.event, 'webhookId': 'another-hook'},
                      {**self.event, 'invoiceId': '../wallet'},
                      {**self.event, 'originalDeliveryId': 'bad/id'}]:
            with self.subTest(event=event), patch('catalog.payments.persist_receipt') as persist:
                self.assertEqual(self.post(event=event).status_code, 400)
                persist.assert_not_called()
        self.assertEqual(self.post(raw=b'not-json').status_code, 400)

    def test_payload_size_is_bounded_and_other_event_types_ignored(self):
        self.assertEqual(self.post(raw=b'x' * (payments.MAX_WEBHOOK_BYTES + 1)).status_code, 413)
        self.assertEqual(self.client.post('/billing/btcpay/webhook/', 'a=1', content_type='text/plain').status_code, 415)
        with patch('catalog.payments.persist_receipt') as persist:
            self.assertEqual(self.post(event={**self.event, 'type': 'PayoutCreated'}).status_code, 200)
            persist.assert_not_called()

    def test_storage_and_provider_failures_request_redelivery(self):
        for exception in [payments.PaymentError('provider'), payments.PaymentError('database')]:
            with patch('catalog.payments.persist_receipt', side_effect=exception):
                self.assertEqual(self.post().status_code, 503)
        with patch('catalog.payments.provider_request', return_value=self.current), \
                patch('catalog.payments.httpx.post', return_value=httpx.Response(503, text='private error')):
            r = self.post()
            self.assertEqual(r.status_code, 503)
            self.assertNotIn('private error', r.content.decode())

    def test_invoice_identity_and_decimal_validation(self):
        for change in [{'id': 'wrong'}, {'storeId': 'other'}, {'amount': '-1'},
                       {'paidAmount': 'NaN'}, {'currency': 'bad currency'}, {'status': 'Unknown'}]:
            with self.subTest(change=change), patch('catalog.payments.provider_request', return_value={**self.current, **change}), \
                    patch('catalog.payments.httpx.post') as post:
                self.assertEqual(self.post().status_code, 503)
                post.assert_not_called()
        for value in ['NaN', 'Infinity', '-1', '1e-999999999', True, {}, '1' * 100]:
            with self.subTest(value=value), self.assertRaises(payments.PaymentError):
                payments.decimal_amount(value)
        self.assertEqual(payments.decimal_amount('0.00000001'), '0.00000001')

    def test_provider_never_follows_redirects_or_leaks_upstream_errors(self):
        for response in [httpx.Response(302, headers={'Location': 'https://another.example'}),
                         httpx.Response(500, text='provider-secret'), httpx.Response(200, text='invalid')]:
            with patch('catalog.payments.httpx.request', return_value=response) as call:
                with self.assertRaises(payments.PaymentError) as error:
                    payments.payment_methods()
                self.assertNotIn('provider-secret', str(error.exception))
                self.assertFalse(call.call_args.kwargs['follow_redirects'])
                self.assertEqual(call.call_args.args[1], 'https://pay.example/api/v1/stores/store1/payment-methods')

    def test_plain_http_credentials_and_unexpected_origins_are_rejected(self):
        for origin in ['http://pay.example', 'https://user:pass@pay.example',
                       'https://pay.example:444', 'https://pay.example/path', 'https://pay.example?next=1']:
            with self.subTest(origin=origin), self.assertRaises(payments.PaymentError):
                payments.secure_origin(origin)
