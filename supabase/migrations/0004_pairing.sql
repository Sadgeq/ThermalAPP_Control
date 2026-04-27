-- ThermalControl: Device pairing
-- ==============================
-- A signed-in mobile user generates a short-lived 6-character code; the
-- agent on the PC types it on first run to claim itself for that user.
-- No .env credentials, no shared service role.
--
-- Trust mechanic: the mobile client snapshots its current auth tokens
-- (refresh AND access — at least one must be present) into the
-- `pairings` row. The agent, possessing the code, exchanges it via
-- claim_pairing() and receives the tokens back. It uses refresh_token if
-- available (long-lived session via auth.refresh_session) and falls back
-- to access_token (~1h, requires re-pair when it expires) — necessary
-- because some Supabase OAuth providers (Google, default config) don't
-- emit a refresh_token at all.
--
-- The `pairings` table is not directly accessible to clients. RLS denies
-- everything; only the SECURITY DEFINER RPCs below can touch it.
--
-- Idempotent: re-applying this file is safe.

-- -----------------------------------------------------------------------
-- Table
-- -----------------------------------------------------------------------
create table if not exists public.pairings (
  code           text primary key,
  user_id        uuid not null references auth.users(id) on delete cascade,
  refresh_token  text,
  access_token   text,
  expires_at     timestamptz not null,
  claimed_at     timestamptz,
  device_id      uuid references public.devices(id) on delete set null,
  attempts       int not null default 0,
  created_at     timestamptz not null default now()
);

-- If an older revision of this migration was applied with refresh_token
-- NOT NULL, relax it.
alter table public.pairings
  alter column refresh_token drop not null;

alter table public.pairings
  add column if not exists access_token text;

create index if not exists pairings_user_idx on public.pairings (user_id, claimed_at);
create index if not exists pairings_expires_idx on public.pairings (expires_at);

alter table public.pairings enable row level security;
-- No policies. Direct table access stays denied; only RPCs below operate on it.
revoke all on public.pairings from anon, authenticated;

-- -----------------------------------------------------------------------
-- Helper: generate a friendly 6-char code from a 32-char alphabet
-- (no I, O, 0, 1 — avoids visual ambiguity)
-- -----------------------------------------------------------------------
create or replace function public._gen_pairing_code()
returns text
language plpgsql
volatile
as $$
declare
  alphabet text := 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  out_code text := '';
  i int;
begin
  for i in 1..6 loop
    out_code := out_code || substr(alphabet, 1 + (random() * 31)::int, 1);
  end loop;
  return out_code;
end;
$$;

-- -----------------------------------------------------------------------
-- generate_pairing_code(refresh_token, access_token)
-- --------------------------------------------------
-- Caller: signed-in mobile client.
-- Stores whichever auth tokens the client has into a fresh pairing row,
-- returns the 6-char code. Any prior unclaimed code for this user is
-- invalidated. At least one token must be non-empty.
-- -----------------------------------------------------------------------
drop function if exists public.generate_pairing_code(text);
drop function if exists public.generate_pairing_code(text, text);

create function public.generate_pairing_code(
  p_refresh_token text default null,
  p_access_token  text default null
)
returns table (code text, expires_at timestamptz)
language plpgsql
security definer
set search_path = public
as $$
declare
  uid   uuid := auth.uid();
  c     text;
  exp   timestamptz := now() + interval '10 minutes';
  tries int := 0;
  r_tok text := nullif(p_refresh_token, '');
  a_tok text := nullif(p_access_token,  '');
begin
  if uid is null then
    raise exception 'Authentication required'
      using errcode = 'insufficient_privilege';
  end if;

  if r_tok is null and a_tok is null then
    raise exception 'No auth token available — sign out and sign in again, then retry';
  end if;

  -- Invalidate any prior unclaimed codes for this user.
  delete from public.pairings
   where pairings.user_id = uid and pairings.claimed_at is null;

  -- Generate a unique code (collisions vanishingly rare in 32^6 space).
  loop
    c := public._gen_pairing_code();
    begin
      insert into public.pairings (code, user_id, refresh_token, access_token, expires_at)
      values (c, uid, r_tok, a_tok, exp);
      exit;
    exception when unique_violation then
      tries := tries + 1;
      if tries > 5 then
        raise exception 'Could not generate a unique code';
      end if;
    end;
  end loop;

  return query select c, exp;
end;
$$;

revoke all on function public.generate_pairing_code(text, text) from public;
grant execute on function public.generate_pairing_code(text, text) to authenticated;

-- -----------------------------------------------------------------------
-- claim_pairing(code, hardware_id, name, os_info)
-- -----------------------------------------------
-- Caller: anonymous (the agent has no JWT yet on first run).
-- Validates code, registers the device under the pairing's user_id, marks
-- the pairing claimed, returns {device_id, user_id, refresh_token, access_token}.
-- -----------------------------------------------------------------------
drop function if exists public.claim_pairing(text, text, text, text);

create function public.claim_pairing(
  p_code        text,
  p_hardware_id text,
  p_name        text default 'PC',
  p_os_info     text default ''
)
returns table (
  device_id     uuid,
  user_id       uuid,
  refresh_token text,
  access_token  text
)
language plpgsql
security definer
set search_path = public
as $$
-- The RETURNS TABLE OUT params (device_id, user_id, refresh_token,
-- access_token) become local variables that shadow column names. To
-- avoid PL/pgSQL silently using the NULL variables instead of column
-- values, prefer columns in name conflicts, AND copy record fields into
-- locally-named variables before any UPDATE/INSERT.
#variable_conflict use_column
declare
  r           public.pairings%rowtype;
  d_id        uuid;
  v_user_id   uuid;
  v_refresh   text;
  v_access    text;
  s_name      text := substr(coalesce(p_name, 'PC'), 1, 64);
  s_os        text := substr(coalesce(p_os_info, ''), 1, 128);
begin
  p_code := upper(regexp_replace(coalesce(p_code, ''), '\s|-', '', 'g'));
  if length(p_code) <> 6 then
    raise exception 'Invalid pairing code format';
  end if;

  if p_hardware_id is null or p_hardware_id !~ '^[0-9a-fA-F]{16,64}$' then
    raise exception 'Invalid hardware_id';
  end if;

  -- Lock the pairing row so concurrent claim attempts can't race.
  select * into r
    from public.pairings
   where pairings.code = p_code
   for update;

  if not found then
    raise exception 'Pairing code not found';
  end if;

  update public.pairings
     set attempts = pairings.attempts + 1
   where pairings.code = p_code;

  if r.attempts + 1 > 5 then
    raise exception 'Too many attempts; request a new code';
  end if;

  if r.claimed_at is not null then
    raise exception 'Pairing code already used';
  end if;

  if r.expires_at < now() then
    raise exception 'Pairing code expired';
  end if;

  -- Pull record fields into explicit locals so subsequent SQL never
  -- ambiguously resolves them against the OUT params.
  v_user_id := r.user_id;
  v_refresh := r.refresh_token;
  v_access  := r.access_token;

  -- Look up by hardware_id alone (it's a unique key). If the row exists
  -- under any user, re-bind it to the pairing's user_id — the human has
  -- physical access to the machine and a valid pairing code, which is
  -- enough to take ownership. Sensor history, profiles, etc. follow the
  -- device via their device_id FK, so the new owner sees them too.
  select devices.id into d_id
    from public.devices
   where devices.hardware_id = p_hardware_id
   limit 1;

  if d_id is not null then
    update public.devices
       set user_id   = v_user_id,
           name      = s_name,
           os_info   = s_os,
           is_online = true,
           last_seen = now()
     where devices.id = d_id;
  else
    insert into public.devices (user_id, hardware_id, name, os_info, is_online, last_seen)
    values (v_user_id, p_hardware_id, s_name, s_os, true, now())
    returning devices.id into d_id;
  end if;

  update public.pairings
     set claimed_at = now(), device_id = d_id
   where pairings.code = p_code;

  return query select d_id, v_user_id, v_refresh, v_access;
end;
$$;

revoke all on function public.claim_pairing(text, text, text, text) from public;
grant execute on function public.claim_pairing(text, text, text, text) to anon, authenticated;

-- -----------------------------------------------------------------------
-- Periodic GC: expire stale codes. Call from a Supabase scheduled task,
-- or rely on lazy GC inside generate_pairing_code (1% sample).
-- -----------------------------------------------------------------------
create or replace function public.purge_expired_pairings()
returns int
language sql
security definer
set search_path = public
as $$
  with deleted as (
    delete from public.pairings
     where (claimed_at is not null and claimed_at < now() - interval '1 day')
        or (claimed_at is null and expires_at < now() - interval '1 hour')
     returning 1
  )
  select count(*)::int from deleted;
$$;

revoke all on function public.purge_expired_pairings() from public;
grant execute on function public.purge_expired_pairings() to authenticated;
