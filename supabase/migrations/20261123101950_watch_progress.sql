create table public.watch_progress (
  user_id uuid not null references auth.users(id) on delete cascade,
  movie_id text not null check (movie_id ~ '^[a-z0-9][a-z0-9-]{0,99}$'),
  position_seconds integer not null,
  duration_seconds integer not null check (duration_seconds between 1 and 86400),
  observed_at timestamptz not null,
  primary key (user_id, movie_id),
  check (position_seconds between 0 and duration_seconds)
);

create index watch_progress_user_recent on public.watch_progress (user_id, observed_at desc);
alter table public.watch_progress enable row level security;
revoke all on public.watch_progress from public, anon, authenticated;
grant select, insert, update on public.watch_progress to authenticated;
create policy watch_progress_read on public.watch_progress for select to authenticated
  using ((select auth.uid()) = user_id);
create policy watch_progress_insert on public.watch_progress for insert to authenticated
  with check ((select auth.uid()) = user_id);
create policy watch_progress_update on public.watch_progress for update to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

create function public.record_watch_progress(p_movie_id text, p_position integer, p_duration integer, p_observed_at timestamptz)
returns void
language sql
security invoker
set search_path = ''
as $$
  insert into public.watch_progress (user_id, movie_id, position_seconds, duration_seconds, observed_at)
  values ((select auth.uid()), p_movie_id, p_position, p_duration, least(p_observed_at, statement_timestamp()))
  on conflict (user_id, movie_id) do update
    set position_seconds = excluded.position_seconds,
        duration_seconds = excluded.duration_seconds,
        observed_at = excluded.observed_at
    where watch_progress.observed_at <= excluded.observed_at;
$$;
revoke all on function public.record_watch_progress(text, integer, integer, timestamptz) from public, anon, authenticated;
grant execute on function public.record_watch_progress(text, integer, integer, timestamptz) to authenticated;
