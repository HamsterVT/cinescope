create table public.billing_orders (
 id uuid primary key, user_id uuid not null references auth.users(id) on delete restrict,
 plan text not null check(plan in ('monthly','annual')), months integer not null check(months in (1,12)),
 amount numeric not null, currency text not null check(currency='KZT'), store_id text not null,
 invoice_id text unique, status text not null default 'Creating', additional_status text not null default 'None',
 created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
 terms_version text not null default 'supporter-2026-11-09', accepted_at timestamptz not null default now(),
 fulfilled_at timestamptz, access_until timestamptz, needs_review boolean not null default false,
 check((plan='monthly' and months=1 and amount=1990) or (plan='annual' and months=12 and amount=15990)),
 check(status in ('Creating','New','Processing','Expired','Invalid','Settled','Error'))
);
create index billing_orders_owner on public.billing_orders(user_id,created_at desc);
create table public.billing_memberships (
 user_id uuid primary key references auth.users(id) on delete restrict,
 valid_until timestamptz not null, blocked boolean not null default false,
 updated_at timestamptz not null default now()
);
alter table public.billing_orders enable row level security;
alter table public.billing_memberships enable row level security;
revoke all on public.billing_orders, public.billing_memberships from public, anon, authenticated, service_role;
grant select on public.billing_orders, public.billing_memberships to authenticated;
grant select,insert,update on public.billing_orders, public.billing_memberships to service_role;
create policy order_owner_read on public.billing_orders for select to authenticated using ((select auth.uid())=user_id);
create policy membership_owner_read on public.billing_memberships for select to authenticated using ((select auth.uid())=user_id);
create policy order_server on public.billing_orders for all to service_role using(true) with check(true);
create policy membership_server on public.billing_memberships for all to service_role using(true) with check(true);

-- Service-only, invoker functions keep normal table privilege checks in force.
create function public.billing_reserve(p_id uuid,p_user uuid,p_plan text,p_store text)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare o public.billing_orders; fresh boolean:=false;
begin
 perform pg_advisory_xact_lock(hashtextextended(p_user::text,0));
 select * into o from public.billing_orders where id=p_id;
 if found then
   if o.user_id<>p_user or o.plan<>p_plan or o.store_id<>p_store then raise exception 'order_conflict'; end if;
   return jsonb_build_object('created',false,'order',to_jsonb(o));
 end if;
 if p_plan not in ('monthly','annual') or length(p_store)>128 or length(p_store)=0 then raise exception 'invalid_plan'; end if;
 if (select count(*) from public.billing_orders where user_id=p_user and created_at>now()-interval '15 minutes')>=5 then raise exception 'too_many_orders'; end if;
 insert into public.billing_orders(id,user_id,plan,months,amount,currency,store_id)
 values(p_id,p_user,p_plan,case when p_plan='monthly' then 1 else 12 end,case when p_plan='monthly' then 1990 else 15990 end,'KZT',p_store) returning * into o;
 return jsonb_build_object('created',true,'order',to_jsonb(o));
end $$;

create function public.billing_reconcile(p_order uuid,p_invoice text,p_store text,p_amount numeric,p_currency text,p_status text,p_additional text,p_paid numeric)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare o public.billing_orders; until_at timestamptz;
begin
 select * into o from public.billing_orders where id=p_order for update;
 if not found then return null; end if;
 if o.store_id<>p_store or o.amount<>p_amount or o.currency<>p_currency or (o.invoice_id is not null and o.invoice_id<>p_invoice)
    or p_invoice is null or length(p_invoice)=0 or p_status not in ('New','Processing','Expired','Invalid','Settled')
    or p_additional not in ('None','PaidLate','PaidPartial','Marked','Invalid','PaidOver') or p_paid<0 then raise exception 'invoice_mismatch'; end if;
 perform pg_advisory_xact_lock(hashtextextended(o.user_id::text,0));
 update public.billing_orders set invoice_id=p_invoice,
   status=case when fulfilled_at is not null and p_status<>'Invalid' then 'Settled' else p_status end,
   additional_status=p_additional,needs_review=(p_additional='Marked' or (p_status='Invalid' and fulfilled_at is not null)),updated_at=now()
   where id=p_order returning * into o;
 if p_status='Invalid' and o.fulfilled_at is not null then
   update public.billing_memberships set blocked=true,updated_at=now() where user_id=o.user_id;
 elsif p_status='Settled' and p_additional not in ('Marked','PaidPartial','Invalid') and p_paid>=o.amount and o.fulfilled_at is null then
   select greatest(valid_until,now()) into until_at from public.billing_memberships where user_id=o.user_id;
   until_at:=coalesce(until_at,now())+make_interval(months=>o.months);
   insert into public.billing_memberships(user_id,valid_until) values(o.user_id,until_at)
     on conflict(user_id) do update set valid_until=excluded.valid_until,updated_at=now();
   update public.billing_orders set fulfilled_at=now(),access_until=until_at,needs_review=false where id=p_order returning * into o;
 end if;
 if p_status='Settled' and p_additional not in ('Marked','PaidPartial','Invalid') and p_paid>=o.amount then
   update public.billing_memberships set blocked=exists(select 1 from public.billing_orders where user_id=o.user_id and fulfilled_at is not null and needs_review),updated_at=now() where user_id=o.user_id;
 end if;
 return to_jsonb(o);
end $$;
revoke all on function public.billing_reserve(uuid,uuid,text,text) from public,anon,authenticated;
revoke all on function public.billing_reconcile(uuid,text,text,numeric,text,text,text,numeric) from public,anon,authenticated;
grant execute on function public.billing_reserve(uuid,uuid,text,text) to service_role;
grant execute on function public.billing_reconcile(uuid,text,text,numeric,text,text,text,numeric) to service_role;
