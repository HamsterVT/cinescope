-- Roles can only be provisioned through the trusted database administration path.
create table public.user_roles (
 user_id uuid primary key references auth.users(id) on delete cascade,
 role text not null check(role in ('user','admin')),
 created_at timestamptz not null default now()
);
alter table public.user_roles enable row level security;
revoke all on public.user_roles from anon,authenticated;
grant select on public.user_roles to authenticated;
create policy role_read_self on public.user_roles for select to authenticated
 using (user_id=(select auth.uid()));

create function public.is_cinescope_admin() returns boolean
 language sql stable security invoker set search_path=''
 as $$select exists(select 1 from public.user_roles where user_id=(select auth.uid()) and role='admin')$$;
revoke all on function public.is_cinescope_admin() from public,anon;
grant execute on function public.is_cinescope_admin() to authenticated;

create table public.content_visibility (
 kind text not null check(kind in ('review','comment')),
 target uuid not null,
 hidden boolean not null default true,
 changed_by uuid not null references auth.users(id),
 updated_at timestamptz not null default now(),
 primary key(kind,target)
);
alter table public.content_visibility enable row level security;
revoke all on public.content_visibility from anon,authenticated;
grant select on public.content_visibility to anon,authenticated;
grant insert,update on public.content_visibility to authenticated;
create policy visibility_read on public.content_visibility for select using(true);
create policy visibility_create on public.content_visibility for insert to authenticated
 with check((select public.is_cinescope_admin()) and changed_by=(select auth.uid()));
create policy visibility_edit on public.content_visibility for update to authenticated
 using((select public.is_cinescope_admin()))
 with check((select public.is_cinescope_admin()) and changed_by=(select auth.uid()));

drop policy reviews_read on public.reviews;
create policy reviews_read on public.reviews for select using (
 user_id=(select auth.uid()) or
 (exists(select 1 from public.profiles p where p.id=user_id and p.is_public)
  and not exists(select 1 from public.content_visibility v where v.kind='review' and v.target=id and v.hidden))
);
create policy reviews_admin_read on public.reviews for select to authenticated using (
 (select public.is_cinescope_admin()) and exists(select 1 from public.profiles p where p.id=user_id and p.is_public));

drop policy comments_read on public.review_comments;
create policy comments_read on public.review_comments for select using (
 user_id=(select auth.uid()) or
 (exists(select 1 from public.reviews r where r.id=review_id)
  and exists(select 1 from public.profiles p where p.id=user_id and p.is_public)
  and not exists(select 1 from public.content_visibility v where v.kind='comment' and v.target=id and v.hidden))
);
create policy comments_admin_read on public.review_comments for select to authenticated using (
 (select public.is_cinescope_admin()) and exists(select 1 from public.profiles p where p.id=user_id and p.is_public)
 and exists(select 1 from public.reviews r join public.profiles p on p.id=r.user_id where r.id=review_id and p.is_public));
