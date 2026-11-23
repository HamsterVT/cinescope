create table public.video_sources (
  movie_id text primary key check (movie_id ~ '^[a-z0-9][a-z0-9-]{0,99}$'),
  movie_title text not null check (char_length(movie_title) between 1 and 250),
  movie_year smallint check (movie_year between 1888 and 2200),
  source_name text not null check (char_length(source_name) between 1 and 120),
  source_url text not null check (source_url like 'https://%' and char_length(source_url) <= 1000),
  media_url text not null default '' check (media_url = '' or (media_url like 'https://%' and char_length(media_url) <= 1000)),
  audio_label text not null default '' check (char_length(audio_label) <= 120),
  published boolean not null default false,
  updated_at timestamptz not null default now()
);

alter table public.video_sources enable row level security;
revoke all on public.video_sources from public, anon, authenticated;
grant select on public.video_sources to anon, authenticated;
grant insert, update on public.video_sources to authenticated;

create policy published_video_sources on public.video_sources for select to anon, authenticated
  using (published);
create policy admin_video_sources on public.video_sources for select to authenticated
  using ((select public.is_cinescope_admin()));
create policy admin_video_insert on public.video_sources for insert to authenticated
  with check ((select public.is_cinescope_admin()));
create policy admin_video_update on public.video_sources for update to authenticated
  using ((select public.is_cinescope_admin()))
  with check ((select public.is_cinescope_admin()));
