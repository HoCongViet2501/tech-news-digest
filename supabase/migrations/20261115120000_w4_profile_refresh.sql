-- Keep profiles.user_vec in step with what it is built from (docs/PLAN.md §15 Cold start).
--
-- The ingest run sets user_vec = normalize(text embedding + follow_centroid) for profiles
-- with profile_text and profile_embedded_at null. Once user_vec exists, the ranking uses
-- it instead of the live follow_centroid, so it must be rebuilt when either input changes:
--   * profile_text changes: profiles_touch already clears profile_embedded_at. When the
--     text is cleared, user_vec is cleared too (nothing to re-embed; the ranking falls
--     back to the live centroid).
--   * the followed tags change: the trigger below clears profile_embedded_at, so the next
--     ingest run re-embeds the text with the new centroid. It touches the row even when
--     it is already pending, so updated_at moves on and an ingest run that read the old
--     centroid does not save its vector (save_profile_vector compares updated_at).

create or replace function private.profiles_touch()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at := now();
  if new.profile_text is distinct from old.profile_text then
    new.profile_embedded_at := null;
    if new.profile_text is null then
      new.user_vec := null;
    end if;
  end if;
  return new;
end;
$$;

-- SECURITY DEFINER: user_tags rows are also deleted by the auth.users cascade, which runs
-- as supabase_auth_admin.
create function private.user_tags_follow_changed()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_user uuid;
begin
  if tg_op = 'INSERT' then
    if new.state <> 'follow' then return null; end if;
    v_user := new.user_id;
  elsif tg_op = 'DELETE' then
    if old.state <> 'follow' then return null; end if;
    v_user := old.user_id;
  else
    if new.state = old.state and new.tag = old.tag then return null; end if;
    if new.state <> 'follow' and old.state <> 'follow' then return null; end if;
    v_user := new.user_id;
  end if;

  update public.profiles p set profile_embedded_at = null
  where p.user_id = v_user and p.profile_text is not null;
  return null;
end;
$$;

create trigger user_tags_follow_changed after insert or update or delete on public.user_tags
  for each row execute function private.user_tags_follow_changed();

revoke all on function private.user_tags_follow_changed()
  from public, anon, authenticated, service_role;
