-- ThermalControl: Baseline RLS policies
-- =====================================
-- Apply via: supabase db push  OR  paste into Supabase SQL editor
--
-- Idempotent: every CREATE is preceded by DROP IF EXISTS, so this can be
-- re-run any time you change a policy.
--
-- Security model
--   * Every domain table is scoped to (auth.uid(), devices.user_id).
--   * Helper is_device_owner(uuid) centralizes the join — STABLE so
--     PostgREST caches it within a request.
--   * No DELETE policies on append-only tables (sensor_readings, alerts).

-- -----------------------------------------------------------------------
-- Helper
-- -----------------------------------------------------------------------
create or replace function public.is_device_owner(p_device_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.devices
    where id = p_device_id
      and user_id = auth.uid()
  );
$$;

revoke all on function public.is_device_owner(uuid) from public;
grant execute on function public.is_device_owner(uuid) to authenticated;

-- -----------------------------------------------------------------------
-- devices
-- -----------------------------------------------------------------------
alter table public.devices enable row level security;

drop policy if exists "devices_select_own"  on public.devices;
drop policy if exists "devices_insert_self" on public.devices;
drop policy if exists "devices_update_own"  on public.devices;
drop policy if exists "devices_delete_own"  on public.devices;

create policy "devices_select_own"
  on public.devices for select
  using (user_id = auth.uid());

-- The pairing RPC (added in 0004_pairing.sql) creates rows on behalf of users.
-- Direct inserts are still allowed but only for one's own user_id.
create policy "devices_insert_self"
  on public.devices for insert
  with check (user_id = auth.uid());

create policy "devices_update_own"
  on public.devices for update
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

create policy "devices_delete_own"
  on public.devices for delete
  using (user_id = auth.uid());

-- -----------------------------------------------------------------------
-- sensor_readings (append-only)
-- -----------------------------------------------------------------------
alter table public.sensor_readings enable row level security;

drop policy if exists "sensor_readings_select_own" on public.sensor_readings;
drop policy if exists "sensor_readings_insert_own" on public.sensor_readings;

create policy "sensor_readings_select_own"
  on public.sensor_readings for select
  using (public.is_device_owner(device_id));

create policy "sensor_readings_insert_own"
  on public.sensor_readings for insert
  with check (public.is_device_owner(device_id));

-- -----------------------------------------------------------------------
-- profiles
-- -----------------------------------------------------------------------
alter table public.profiles enable row level security;

drop policy if exists "profiles_select_own" on public.profiles;
drop policy if exists "profiles_insert_own" on public.profiles;
drop policy if exists "profiles_update_own" on public.profiles;
drop policy if exists "profiles_delete_own" on public.profiles;

create policy "profiles_select_own"
  on public.profiles for select
  using (public.is_device_owner(device_id));

create policy "profiles_insert_own"
  on public.profiles for insert
  with check (public.is_device_owner(device_id));

create policy "profiles_update_own"
  on public.profiles for update
  using (public.is_device_owner(device_id))
  with check (public.is_device_owner(device_id));

create policy "profiles_delete_own"
  on public.profiles for delete
  using (public.is_device_owner(device_id));

-- -----------------------------------------------------------------------
-- commands
-- -----------------------------------------------------------------------
alter table public.commands enable row level security;

drop policy if exists "commands_select_own" on public.commands;
drop policy if exists "commands_insert_own" on public.commands;
drop policy if exists "commands_update_own" on public.commands;

create policy "commands_select_own"
  on public.commands for select
  using (public.is_device_owner(device_id));

create policy "commands_insert_own"
  on public.commands for insert
  with check (public.is_device_owner(device_id));

-- Only the agent should mark commands executed/failed. We don't differentiate
-- agent-vs-mobile by JWT today, so allow update from any owner. Tighten later
-- by signing per-device JWTs (phase 1).
create policy "commands_update_own"
  on public.commands for update
  using (public.is_device_owner(device_id))
  with check (public.is_device_owner(device_id));

-- -----------------------------------------------------------------------
-- alert_settings
-- -----------------------------------------------------------------------
alter table public.alert_settings enable row level security;

drop policy if exists "alert_settings_select_own" on public.alert_settings;
drop policy if exists "alert_settings_upsert_own" on public.alert_settings;
drop policy if exists "alert_settings_update_own" on public.alert_settings;
drop policy if exists "alert_settings_delete_own" on public.alert_settings;

create policy "alert_settings_select_own"
  on public.alert_settings for select
  using (public.is_device_owner(device_id));

create policy "alert_settings_upsert_own"
  on public.alert_settings for insert
  with check (public.is_device_owner(device_id));

create policy "alert_settings_update_own"
  on public.alert_settings for update
  using (public.is_device_owner(device_id))
  with check (public.is_device_owner(device_id));

create policy "alert_settings_delete_own"
  on public.alert_settings for delete
  using (public.is_device_owner(device_id));

-- -----------------------------------------------------------------------
-- alerts (append-only)
-- -----------------------------------------------------------------------
alter table public.alerts enable row level security;

drop policy if exists "alerts_select_own" on public.alerts;
drop policy if exists "alerts_insert_own" on public.alerts;

create policy "alerts_select_own"
  on public.alerts for select
  using (public.is_device_owner(device_id));

create policy "alerts_insert_own"
  on public.alerts for insert
  with check (public.is_device_owner(device_id));

-- -----------------------------------------------------------------------
-- Realtime publication
-- -----------------------------------------------------------------------
-- Make sure tables the clients subscribe to are part of the supabase_realtime
-- publication. Idempotent: ADD will error if already present, so we wrap.
do $$
begin
  begin
    alter publication supabase_realtime add table public.sensor_readings;
  exception when duplicate_object then null;
  end;
  begin
    alter publication supabase_realtime add table public.profiles;
  exception when duplicate_object then null;
  end;
  begin
    alter publication supabase_realtime add table public.commands;
  exception when duplicate_object then null;
  end;
  begin
    alter publication supabase_realtime add table public.alerts;
  exception when duplicate_object then null;
  end;
end $$;
