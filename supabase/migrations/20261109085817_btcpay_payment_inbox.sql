-- Server-only receipt inbox. This does not create orders or paid entitlements.
create table public.billing_receipts (
    store_id text not null check (store_id ~ '^[A-Za-z0-9_-]{1,128}$'),
    delivery_id text not null check (delivery_id ~ '^[A-Za-z0-9_-]{1,128}$'),
    original_delivery_id text not null check (original_delivery_id ~ '^[A-Za-z0-9_-]{1,128}$'),
    invoice_id text not null check (invoice_id ~ '^[A-Za-z0-9_-]{1,128}$'),
    event_type text not null check (event_type in ('InvoiceCreated','InvoiceReceivedPayment','InvoiceProcessing','InvoiceExpired','InvoiceSettled','InvoiceInvalid','InvoicePaymentSettled')),
    invoice_status text not null check (invoice_status in ('New','Processing','Expired','Invalid','Settled')),
    additional_status text not null check (additional_status in ('None','PaidLate','PaidPartial','Marked','Invalid','PaidOver')),
    amount numeric not null check (amount >= 0 and amount <= 1000000000000000000),
    paid_amount numeric not null check (paid_amount >= 0 and paid_amount <= 1000000000000000000),
    currency text not null check (currency ~ '^[A-Z0-9]{2,16}$'),
    body_sha256 text not null check (body_sha256 ~ '^[0-9a-f]{64}$'),
    received_at timestamptz not null default now(),
    primary key (store_id, delivery_id)
);
create index billing_receipts_invoice on public.billing_receipts (store_id, invoice_id, received_at);
alter table public.billing_receipts enable row level security;
revoke all on public.billing_receipts from public, anon, authenticated, service_role;
grant select, insert on public.billing_receipts to service_role;
create policy billing_server_insert on public.billing_receipts for insert to service_role with check (true);
create policy billing_server_read on public.billing_receipts for select to service_role using (true);
comment on table public.billing_receipts is 'Verified BTCPay notification inbox only; no subscription activation. No payer or raw webhook metadata.';
