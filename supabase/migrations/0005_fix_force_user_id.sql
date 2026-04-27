-- ThermalControl: fix the force_user_id() trigger
-- ================================================
-- The original trigger unconditionally set NEW.user_id = auth.uid()
-- on every INSERT/UPDATE. That's fine for direct user-facing writes
-- (it prevents user_id spoofing), but it breaks any security-definer
-- code path that legitimately needs to set user_id explicitly:
--
--   * claim_pairing(): runs as anon (auth.uid() is NULL), needs to bind
--     a device to the pairing's user_id.
--   * Direct service-role / SQL Editor maintenance UPDATEs.
--
-- Patch: only force user_id when auth.uid() is non-null. Security-
-- definer paths are unaffected, end-user writes are still pinned to
-- their own JWT identity (combined with the RLS policies in
-- 0001_baseline_rls.sql which check user_id = auth.uid() on insert,
-- a malicious user can't impersonate someone else).
--
-- The trigger is left in place; only the function body changes.

create or replace function public.force_user_id()
returns trigger
language plpgsql
security definer
as $$
declare
  uid uuid := auth.uid();
begin
  -- Only stamp on user-context calls. NULL auth.uid() means we're inside
  -- a security-definer function or the SQL Editor — trust the explicit
  -- value the caller provided.
  if uid is not null then
    new.user_id := uid;
  end if;
  return new;
end;
$$;
