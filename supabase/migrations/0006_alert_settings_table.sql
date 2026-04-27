-- ThermalControl: alert_settings table
-- ====================================
-- The RLS policies for public.alert_settings exist in 0001_baseline_rls.sql,
-- but the table itself was never declared in a migration — it must have been
-- created via the Supabase dashboard at some point. This migration backfills
-- the schema so a fresh database can be brought up from migrations alone.
--
-- IF NOT EXISTS makes it safe to apply on a database where the table already
-- exists (e.g. one created via the dashboard). Existing data is left alone.
--
-- The agent reads this table at startup (cloud.py:get_alert_settings) to
-- restore per-device alert thresholds. Columns mirror what the agent reads:
-- see pc-agent/Backend/agent.py:_check_alerts and the in-memory shape at
-- agent.py:274-284.

create table if not exists public.alert_settings (
  id               uuid primary key default gen_random_uuid(),
  device_id        uuid not null references public.devices(id) on delete cascade,
  metric           text not null check (metric in ('cpu_temp', 'gpu_temp')),
  threshold        numeric(5,1) not null check (threshold between 30 and 120),
  enabled          boolean not null default true,
  cooldown_minutes int not null default 5 check (cooldown_minutes between 1 and 60),
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  -- One row per (device, metric). The agent upserts on this key.
  unique (device_id, metric)
);

-- Index for the hot read path: select * from alert_settings where device_id=$1
create index if not exists alert_settings_device_id_idx
  on public.alert_settings(device_id);

-- Touch updated_at on every UPDATE.
create or replace function public.touch_alert_settings_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists trg_alert_settings_updated_at on public.alert_settings;
create trigger trg_alert_settings_updated_at
  before update on public.alert_settings
  for each row execute function public.touch_alert_settings_updated_at();
