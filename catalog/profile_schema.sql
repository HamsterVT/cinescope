-- Additive social profile schema. Existing libraries remain private by default.
create table public.profiles (
 id uuid primary key references auth.users(id) on delete cascade,
 display_name text not null check (char_length(display_name) between 1 and 60),
 bio text not null default '' check (char_length(bio)<=280),
 avatar_data text not null default '' check (octet_length(avatar_data)<=100000),
 cover_id text not null default 'interstellar' check (char_length(cover_id)<=100),
 is_public boolean not null default false,
 stars text[] not null default '{}' check (cardinality(stars)<=24),
 created_at timestamptz not null default now()
);
alter table public.profiles enable row level security;
revoke all on public.profiles from anon, authenticated;
grant select on public.profiles to anon, authenticated;
grant insert,update on public.profiles to authenticated;
create policy profiles_read on public.profiles for select using (is_public or id=(select auth.uid()));
create policy profiles_create on public.profiles for insert to authenticated with check (id=(select auth.uid()));
create policy profiles_edit on public.profiles for update to authenticated using (id=(select auth.uid())) with check (id=(select auth.uid()));

create table public.follows (
 follower_id uuid not null references public.profiles(id) on delete cascade,
 following_id uuid not null references public.profiles(id) on delete cascade,
 created_at timestamptz not null default now(),
 primary key(follower_id,following_id), check(follower_id<>following_id)
);
create index follows_target on public.follows(following_id);
alter table public.follows enable row level security;
revoke all on public.follows from anon,authenticated;
grant select on public.follows to anon,authenticated;
grant insert,delete on public.follows to authenticated;
create policy follows_read on public.follows for select using (
 follower_id=(select auth.uid()) or following_id=(select auth.uid()) or
 (exists(select 1 from public.profiles p where p.id=follower_id and p.is_public)
 and exists(select 1 from public.profiles p where p.id=following_id and p.is_public)));
create policy follows_create on public.follows for insert to authenticated with check (
 follower_id=(select auth.uid()) and exists(select 1 from public.profiles p where p.id=following_id and p.is_public));
create policy follows_remove on public.follows for delete to authenticated using (follower_id=(select auth.uid()));

create table public.reviews (
 id uuid primary key default gen_random_uuid(),
 user_id uuid not null references public.profiles(id) on delete cascade,
 movie_id text not null check(char_length(movie_id) between 1 and 100),
 body text not null check(char_length(body) between 20 and 5000),
 spoiler boolean not null default false,
 updated_at timestamptz not null default now(),
 unique(user_id,movie_id)
);
create index reviews_recent on public.reviews(updated_at desc);
alter table public.reviews enable row level security;
revoke all on public.reviews from anon,authenticated;
grant select on public.reviews to anon,authenticated;
grant insert,update,delete on public.reviews to authenticated;
create policy reviews_read on public.reviews for select using (user_id=(select auth.uid()) or exists(select 1 from public.profiles p where p.id=user_id and p.is_public));
create policy reviews_create on public.reviews for insert to authenticated with check (user_id=(select auth.uid()));
create policy reviews_edit on public.reviews for update to authenticated using(user_id=(select auth.uid())) with check(user_id=(select auth.uid()));
create policy reviews_remove on public.reviews for delete to authenticated using(user_id=(select auth.uid()));

create table public.review_comments (
 id uuid primary key default gen_random_uuid(),
 review_id uuid not null references public.reviews(id) on delete cascade,
 user_id uuid not null references public.profiles(id) on delete cascade,
 body text not null check(char_length(body) between 1 and 1000),
 created_at timestamptz not null default now()
);
create index comments_review on public.review_comments(review_id,created_at);
create index comments_author on public.review_comments(user_id,created_at desc);
alter table public.review_comments enable row level security;
revoke all on public.review_comments from anon,authenticated;
grant select on public.review_comments to anon,authenticated;
grant insert,delete on public.review_comments to authenticated;
create policy comments_read on public.review_comments for select using (
 exists(select 1 from public.reviews r where r.id=review_id) and
 (user_id=(select auth.uid()) or exists(select 1 from public.profiles p where p.id=user_id and p.is_public)));
create policy comments_create on public.review_comments for insert to authenticated with check (
 user_id=(select auth.uid()) and exists(select 1 from public.profiles p where p.id=user_id and p.is_public)
 and exists(select 1 from public.reviews r join public.profiles p on p.id=r.user_id where r.id=review_id and p.is_public));
create policy comments_remove on public.review_comments for delete to authenticated using(user_id=(select auth.uid()));

grant select on public.library to anon;
create policy library_public_read on public.library for select to anon,authenticated using (
 exists(select 1 from public.profiles p where p.id=user_id and p.is_public));
