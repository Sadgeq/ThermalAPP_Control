-- ThermalControl: Command audit log
-- =================================
-- Append-only forensic record of every command insert. Captures who issued
-- it (auth.uid()), the client claims, and the full payload. Survives row
-- updates / deletes on the commands table.

create table if not exists public.command_audit (
  id           bigserial primary key,
  command_id   uuid,                    -- nullable: the command row may be deleted
  device_id    uuid not null,
  user_id      uuid,                    -- auth.uid() at insert time
  command_type text not null,
  payload      jsonb,
  jwt_claims   jsonb,                   -- snapshot of auth.jwt()
  created_at   timestamptz not null default now()
);

create index if not exists command_audit_device_idx
  on public.command_audit (device_id, created_at desc);

create index if not exists command_audit_user_idx
  on public.command_audit (user_id, created_at desc);

-- RLS: owners can read their own audit rows. Nobody can insert directly
-- (only the trigger below, which runs as security definer). No update/delete.
alter table public.command_audit enable row level security;

drop policy if exists "command_audit_select_own" on public.command_audit;
create policy "command_audit_select_own"
  on public.command_audit for select
  using (public.is_device_owner(device_id));

-- Strip default INSERT/UPDATE/DELETE access; trigger bypasses RLS.
revoke insert, update, delete on public.command_audit from anon, authenticated;

create or replace function public.log_command_insert()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.command_audit (
    command_id, device_id, user_id, command_type, payload, jwt_claims
  ) values (
    new.id,
    new.device_id,
    auth.uid(),
    new.command_type,
    new.payload,
    coalesce(auth.jwt(), '{}'::jsonb)
  );
  return new;
end;
$$;

-- Run after the validator trigger so we don't log rejected rows.
drop trigger if exists trg_log_command_insert on public.commands;
create trigger trg_log_command_insert
  after insert on public.commands
  for each row execute function public.log_command_insert();
