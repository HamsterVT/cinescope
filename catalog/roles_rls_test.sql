-- Every fixture and moderation action is rolled back.
begin;
select set_config('test.admin',gen_random_uuid()::text,true),set_config('test.user',gen_random_uuid()::text,true),set_config('test.private',gen_random_uuid()::text,true),set_config('test.review',gen_random_uuid()::text,true),set_config('test.comment',gen_random_uuid()::text,true);
insert into auth.users(id) values(current_setting('test.admin')::uuid),(current_setting('test.user')::uuid),(current_setting('test.private')::uuid);
insert into public.user_roles(user_id,role) values(current_setting('test.admin')::uuid,'admin');
insert into public.profiles(id,display_name,is_public) values(current_setting('test.user')::uuid,'Public fixture',true),(current_setting('test.private')::uuid,'Private fixture',false);
insert into public.reviews(id,user_id,movie_id,body) values(current_setting('test.review')::uuid,current_setting('test.user')::uuid,'role-public','Public review for role verification.'),(gen_random_uuid(),current_setting('test.private')::uuid,'role-private','Private review for role verification.');
insert into public.review_comments(id,user_id,review_id,body) values(current_setting('test.comment')::uuid,current_setting('test.user')::uuid,current_setting('test.review')::uuid,'Public comment');
select set_config('request.jwt.claims',json_build_object('sub',current_setting('test.user'),'role','authenticated','user_metadata',json_build_object('role','admin'))::text,true);
set local role authenticated;
do $$ begin
 if public.is_cinescope_admin() then raise exception 'Metadata escalates role'; end if;
 begin
  insert into public.user_roles(user_id,role) values(current_setting('test.user')::uuid,'admin');
  raise exception 'User can assign own role';
 exception when insufficient_privilege then null; end;
 begin
  insert into public.content_visibility(kind,target,hidden,changed_by) values('review',current_setting('test.review')::uuid,true,current_setting('test.user')::uuid);
  raise exception 'User can moderate content';
 exception when insufficient_privilege then null; end;
end $$;
reset role;
select set_config('request.jwt.claims',json_build_object('sub',current_setting('test.admin'),'role','authenticated')::text,true);
set local role authenticated;
do $$ begin
 if not public.is_cinescope_admin() then raise exception 'Admin permission missing'; end if;
 if exists(select 1 from public.reviews where movie_id='role-private') then raise exception 'Private content leaked to admin'; end if;
 insert into public.content_visibility(kind,target,hidden,changed_by) values('review',current_setting('test.review')::uuid,true,current_setting('test.admin')::uuid);
 if not exists(select 1 from public.reviews where id=current_setting('test.review')::uuid) then raise exception 'Admin cannot restore hidden review'; end if;
end $$;
reset role;
set local role anon;
do $$ begin
 if exists(select 1 from public.reviews where id=current_setting('test.review')::uuid) then raise exception 'Hidden review leaked'; end if;
 if exists(select 1 from public.review_comments where id=current_setting('test.comment')::uuid) then raise exception 'Hidden discussion leaked'; end if;
end $$;
reset role;
select set_config('request.jwt.claims',json_build_object('sub',current_setting('test.admin'),'role','authenticated')::text,true);
set local role authenticated;
update public.content_visibility set hidden=false where kind='review' and target=current_setting('test.review')::uuid;
insert into public.content_visibility(kind,target,hidden,changed_by) values('comment',current_setting('test.comment')::uuid,true,current_setting('test.admin')::uuid);
reset role;
set local role anon;
do $$ begin
 if not exists(select 1 from public.reviews where id=current_setting('test.review')::uuid) then raise exception 'Restored review unavailable'; end if;
 if exists(select 1 from public.review_comments where id=current_setting('test.comment')::uuid) then raise exception 'Hidden comment leaked'; end if;
end $$;
reset role;
rollback;
select 'PASS: role isolation, no self-promotion, private data, hide/restore, comments' as result;
