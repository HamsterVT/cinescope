from unittest.mock import patch
from uuid import uuid4
from django.core import signing
from django.http import Http404
from django.test import SimpleTestCase, RequestFactory, override_settings
from . import billing
from .payments import BTC_ONCHAIN, InvoiceRejected, PaymentError
from .test_accounts import STORES

USER = 'e470cfac-5607-4be0-803e-ca553367a99f'
BTC_WALLET = [{'enabled': True, 'paymentMethodId': BTC_ONCHAIN}]


@override_settings(STORAGES=STORES, BTCPAY_URL='https://pay.example', BTCPAY_STORE_ID='store1',
                   SUPABASE_BILLING_SECRET_KEY='test-secret', SITE_URL='https://cine.example')
class CheckoutTests(SimpleTestCase):
    def setUp(self):
        self.oid = str(uuid4())
        self.order = {'id': self.oid, 'user_id': USER, 'plan': 'monthly', 'amount': 1990,
                      'currency': 'KZT', 'invoice_id': None, 'status': 'Creating', 'store_id': 'store1'}
        self.current = {'id': 'invoice1', 'storeId': 'store1', 'metadata': {'orderId': self.oid},
                        'amount': '1990', 'currency': 'KZT', 'paidAmount': '0', 'status': 'New',
                        'additionalStatus': 'None', 'checkoutLink': 'https://pay.example/i/invoice1'}

    def request(self, method='post', data=None, user=USER):
        req = getattr(RequestFactory(), method)('/billing/checkout/', data or {})
        req.cloud_user = {'id': user} if user else None
        req.cloud_token = 'user-jwt'
        req.cinescope_role = 'user'
        req.cloud_error = False
        return req

    def payload(self, **changes):
        data = {'plan': 'monthly', 'consent': billing.TERMS_VERSION,
                'checkout_token': signing.dumps({'user': USER,'order': self.oid,'plan':'monthly'}, salt='plus-checkout')}
        return {**data, **changes}

    @patch('catalog.billing.member', return_value=None)
    def test_price_comes_from_server_and_checkout_binds_order(self, member):
        with patch('catalog.billing.api', side_effect=[{'created': True,'order': self.order}, self.order]) as db, \
                patch('catalog.payments.payment_methods', return_value=BTC_WALLET), \
                patch('catalog.payments.provider_request', return_value=self.current) as provider:
            r = billing.checkout(self.request(data=self.payload(amount='1', currency='USD')))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r['Location'], 'https://pay.example/i/invoice1')
        invoice = provider.call_args.kwargs['data']
        self.assertEqual((invoice['amount'],invoice['currency']), ('1990','KZT'))
        self.assertEqual(invoice['metadata']['orderId'], self.oid)
        self.assertEqual(invoice['checkout']['paymentTolerance'], 0)
        self.assertEqual(invoice['checkout']['paymentMethods'], ['BTC-CHAIN'])
        self.assertNotIn('email', invoice['metadata'])
        self.assertEqual(db.call_args_list[1].args[1], 'rpc/billing_reconcile')

    @patch('catalog.billing.member', return_value=None)
    def test_duplicate_submission_never_creates_second_invoice(self, member):
        with patch('catalog.billing.api', return_value={'created': False,'order': self.order}), \
                patch('catalog.payments.payment_methods', return_value=BTC_WALLET), \
                patch('catalog.payments.provider_request') as provider:
            r = billing.checkout(self.request(data=self.payload()))
        self.assertEqual(r['Location'], '/billing/order/' + self.oid + '/')
        provider.assert_not_called()

    @patch('catalog.billing.member', return_value=None)
    def test_missing_wallet_or_consent_and_cross_account_token(self, member):
        with patch('catalog.billing.api') as api, patch('catalog.payments.payment_methods', return_value=[]):
            r = billing.checkout(self.request(data=self.payload()))
            self.assertEqual(r.status_code, 200)
            self.assertIn('Не удалось создать счёт', r.content.decode())
            api.assert_not_called()
        for changes in [{'consent':''},{'checkout_token':'forged'}]:
            with patch('catalog.payments.payment_methods') as methods:
                r = billing.checkout(self.request(data=self.payload(**changes)))
                self.assertEqual(r.status_code, 200)
                methods.assert_not_called()
        with patch('catalog.payments.payment_methods') as methods:
            billing.checkout(self.request(data=self.payload(), user=str(uuid4())))
            methods.assert_not_called()

    def test_guest_reviews_price_and_cannot_create_order(self):
        r = billing.checkout(self.request(method='get', user=None))
        self.assertIn('1 990', r.content.decode())
        self.assertIn('Войти и продолжить', r.content.decode())
        self.assertIn('cs_checkout', r.cookies)
        r = billing.checkout(self.request(data=self.payload(), user=None))
        self.assertEqual(r['Location'], '/account/login/')

    def test_order_return_does_not_fulfil_and_ownership_is_enforced(self):
        req = self.request(method='get', data={'paid':'true'})
        with patch('catalog.billing.owned_order', return_value=self.order), patch('catalog.billing.reconcile') as reconcile:
            r = billing.order_page(req, self.oid)
            self.assertEqual(r.status_code, 200)
            reconcile.assert_not_called()
        with patch('catalog.cloud.api', return_value=[]) as query:
            with self.assertRaises(Http404): billing.owned_order(req, self.oid)
            self.assertEqual(query.call_args.kwargs['token'], 'user-jwt')
            self.assertEqual(query.call_args.kwargs['params']['user_id'], 'eq.'+USER)

    def test_order_refresh_rejects_another_orders_invoice(self):
        req = self.request(data={'action':'refresh'})
        with patch('catalog.billing.owned_order', return_value=self.order), \
                patch('catalog.billing.order_invoice', return_value={**self.current,'metadata':{'orderId':str(uuid4())}}), \
                patch('catalog.billing.reconcile') as reconcile:
            r = billing.order_page(req, self.oid)
            self.assertIn('Повторите', r.content.decode())
            reconcile.assert_not_called()

    def test_no_open_redirect_and_unrelated_invoice_does_not_activate(self):
        for link in ['https://evil.example/i/invoice1', 'http://pay.example/i/invoice1',
                     'https://pay.example@evil.example/i/invoice1', 'https://pay.example/account/',
                     'https://pay.example/i/different_invoice']:
            with self.subTest(link=link), self.assertRaises(PaymentError):
                billing.checkout_url({'id': 'invoice1', 'checkoutLink': link})
        with patch('catalog.billing.api') as api:
            self.assertIsNone(billing.reconcile({**self.current,'metadata':{}}))
            api.assert_not_called()

    @patch('catalog.billing.member', return_value=None)
    def test_only_definite_provider_rejection_marks_order_failed(self, member):
        for failure, writes in [(InvoiceRejected('rejected'), 2), (PaymentError('timeout'), 1)]:
            with self.subTest(failure=failure), \
                    patch('catalog.billing.api', return_value={'created': True, 'order': self.order}) as db, \
                    patch('catalog.payments.payment_methods', return_value=BTC_WALLET), \
                    patch('catalog.payments.provider_request', side_effect=failure):
                response = billing.checkout(self.request(data=self.payload()))
                self.assertEqual(response['Location'], '/billing/order/' + self.oid + '/')
                self.assertEqual(db.call_count, writes)
                if writes == 2:
                    self.assertEqual(db.call_args.args, ('PATCH', 'billing_orders'))
                    self.assertEqual(db.call_args.kwargs['params']['invoice_id'], 'is.null')

    def test_status_poll_only_reads_the_owned_order(self):
        request = self.request(method='get', data={'status': '1'})
        with patch('catalog.billing.owned_order', return_value={**self.order, 'status': 'Processing'}) as read, \
                patch('catalog.billing.order_invoice') as provider:
            response = billing.order_page(request, self.oid)
            read.assert_called_once_with(request, self.oid)
            provider.assert_not_called()
        self.assertJSONEqual(response.content, {'state': 'processing'})
        self.assertIn('no-store', response['Cache-Control'])
        guest = self.request(method='get', data={'status': '1'}, user=None)
        self.assertEqual(billing.order_page(guest, self.oid).status_code, 401)

    def test_partial_or_late_expired_payment_does_not_invite_second_purchase(self):
        for extra, state in [('PaidPartial', 'partial'), ('PaidLate', 'review')]:
            with self.subTest(extra=extra), patch('catalog.billing.owned_order', return_value={
                **self.order, 'status': 'Expired', 'additional_status': extra,
            }):
                response = billing.order_page(self.request(method='get'), self.oid)
                self.assertContains(response, f'data-order-state="{state}"')
                self.assertNotContains(response, 'Оформить новый заказ')

    def test_successful_login_returns_to_signed_plan_only(self):
        from django.shortcuts import redirect
        from .cloud import CloudSessionMiddleware, set_tokens
        guest = billing.checkout(self.request(method='get', data={'plan':'annual'}, user=None))
        req = self.request(method='get', user=None)
        req.COOKIES['cs_checkout'] = guest.cookies['cs_checkout'].value
        middleware = CloudSessionMiddleware(lambda request: set_tokens(redirect('/profile/'),
                                                 {'access_token':'access','refresh_token':'refresh'}))
        with override_settings(SUPABASE_URL='',SUPABASE_PUBLISHABLE_KEY=''):
            r = middleware(req)
            self.assertEqual(r['Location'],'/billing/checkout/?plan=annual')
            req.COOKIES['cs_checkout']='https://evil.example'
            self.assertEqual(middleware(req)['Location'],'/profile/')
