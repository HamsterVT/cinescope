-- Transactional integration test: no test accounts or data survive ROLLBACK.
begin;
select set_config('test.owner',gen_random_uuid()::text,true),set_config('test.other',gen_random_uuid()::text,true);
insert into auth.users(id) values(current_setting('test.owner')::uuid),(current_setting('test.other')::uuid);
insert into public.profiles(id,display_name,is_public) values(current_setting('test.owner')::uuid,'Private test',false),(current_setting('test.other')::uuid,'Public test',true);
insert into public.library(user_id,movie_id,saved,watched,rating,movie) values(current_setting('test.owner')::uuid,'rls-private',true,true,9,'{}'),(current_setting('test.other')::uuid,'rls-public',true,true,8,'{}');
insert into public.reviews(user_id,movie_id,body) values(current_setting('test.owner')::uuid,'rls-private','Private review for RLS verification.'),(current_setting('test.other')::uuid,'rls-public','Public review for RLS verification.');
set local role anon;
do $$ begin
 if exists(select 1 from public.profiles where id=current_setting('test.owner')::uuid) then raise exception 'Private profile leaked'; end if;
 if exists(select 1 from public.library where movie_id='rls-private') then raise exception 'Private library leaked'; end if;
 if exists(select 1 from public.reviews where movie_id='rls-private') then raise exception 'Private review leaked'; end if;
 if not exists(select 1 from public.library where movie_id='rls-public') then raise exception 'Public library missing'; end if;
end $$;
reset role;
select set_config('request.jwt.claims',json_build_object('sub',current_setting('test.owner'),'role','authenticated')::text,true);
set local role authenticated;
do $$ declare n integer; begin
 update public.profiles set bio='forged' where id=current_setting('test.other')::uuid;
 get diagnostics n=row_count;
 if n<>0 then raise exception 'Can edit another profile'; end if;
 update public.profiles set bio='Owner edit' where id=current_setting('test.owner')::uuid;
 get diagnostics n=row_count;
 if n<>1 then raise exception 'Cannot edit own profile'; end if;
 begin
  insert into public.follows(follower_id,following_id) values(current_setting('test.other')::uuid,current_setting('test.owner')::uuid);
  raise exception 'Can forge follow';
 exception when insufficient_privilege then null; end;
 insert into public.follows(follower_id,following_id) values(current_setting('test.owner')::uuid,current_setting('test.other')::uuid);
 begin
  insert into public.reviews(user_id,movie_id,body) values(current_setting('test.other')::uuid,'forged','An invalid review owned by someone else.');
  raise exception 'Can forge review';
 exception when insufficient_privilege then null; end;
 begin
  insert into public.review_comments(user_id,review_id,body) select current_setting('test.owner')::uuid,id,'Private author comment' from public.reviews where movie_id='rls-public';
  raise exception 'Private user can publish comments';
 exception when insufficient_privilege then null; end;
 update public.profiles set is_public=true where id=current_setting('test.owner')::uuid;
 insert into public.review_comments(user_id,review_id,body) select current_setting('test.owner')::uuid,id,'Public author comment' from public.reviews where movie_id='rls-public';
end $$;
reset role;
select set_config('request.jwt.claims',json_build_object('sub',current_setting('test.other'),'role','authenticated')::text,true);
set local role authenticated;
do $$ declare n integer; begin
 delete from public.review_comments where user_id=current_setting('test.owner')::uuid;
 get diagnostics n=row_count;
 if n<>0 then raise exception 'Can delete another comment'; end if;
end $$;
reset role;
rollback;
select 'PASS: private visibility, public visibility, ownership, follows, reviews, comments' as result;
