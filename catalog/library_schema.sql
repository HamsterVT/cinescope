-- Baseline for a new Supabase project. Apply before profile_schema.sql.
create table public.library (
  user_id uuid not null references auth.users(id) on delete cascade,
  movie_id text not null check (char_length(movie_id) between 1 and 100),
  saved boolean not null default false,
  watched boolean not null default false,
  rating integer check (rating between 1 and 10),
  movie jsonb not null check (jsonb_typeof(movie) = 'object' and octet_length(movie::text) < 32768),
  updated_at timestamptz not null default now(),
  primary key (user_id, movie_id)
);

create index library_user_recent on public.library (user_id, updated_at desc);
alter table public.library enable row level security;
revoke all on public.library from public, anon, authenticated;
grant select, insert, update, delete on public.library to authenticated;

create policy library_select on public.library for select to authenticated
  using ((select auth.uid()) = user_id);
create policy library_insert on public.library for insert to authenticated
  with check ((select auth.uid()) = user_id);
create policy library_update on public.library for update to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);
create policy library_delete on public.library for delete to authenticated
  using ((select auth.uid()) = user_id);
