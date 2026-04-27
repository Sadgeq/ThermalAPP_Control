-- ThermalControl: Server-side command validation + rate limit
-- ============================================================
-- Defense in depth. The agent already whitelists command_type and clamps
-- payload, but a stolen mobile JWT could insert anything via PostgREST.
-- This trigger blocks malformed/oversized inserts at the database boundary
-- and rate-limits per user to prevent flooding.

-- -----------------------------------------------------------------------
-- Validator: rejects unknown command_type and malformed payload shapes.
-- Mirrors the whitelist in pc-agent/Backend/agent.py:_handle_command_sync
-- -----------------------------------------------------------------------
create or replace function public.validate_command()
returns trigger
language plpgsql
as $$
declare
  ctype text := new.command_type;
  p     jsonb := coalesce(new.payload, '{}'::jsonb);
begin
  -- Whitelist of accepted command types. Keep in sync with agent.
  if ctype not in (
    'set_fan_speed',
    'set_profile',
    'set_alert_threshold',
    'set_all_fans',
    'set_fan_mode'
  ) then
    raise exception 'commands.command_type % is not allowed', ctype
      using errcode = 'check_violation';
  end if;

  -- Payload must be a JSON object, never an array or scalar.
  if jsonb_typeof(p) <> 'object' then
    raise exception 'commands.payload must be a JSON object'
      using errcode = 'check_violation';
  end if;

  -- Bound the payload size so a client can't park megabytes per row.
  if octet_length(p::text) > 2048 then
    raise exception 'commands.payload too large (>2KB)'
      using errcode = 'check_violation';
  end if;

  -- Per-type shape checks. We validate ranges loosely; the agent re-clamps.
  if ctype = 'set_fan_speed' then
    if not (p ? 'fan_index' and p ? 'speed_percent') then
      raise exception 'set_fan_speed requires fan_index and speed_percent';
    end if;
    if (p->>'fan_index')::int not between 0 and 7 then
      raise exception 'fan_index out of range';
    end if;
    if (p->>'speed_percent')::numeric not between 0 and 100 then
      raise exception 'speed_percent out of range';
    end if;

  elsif ctype = 'set_profile' then
    if not (p ? 'profile_name') then
      raise exception 'set_profile requires profile_name';
    end if;
    if length(p->>'profile_name') > 32 then
      raise exception 'profile_name too long';
    end if;
    if (p->>'profile_name') !~ '^[A-Za-z0-9 _-]{1,32}$' then
      raise exception 'profile_name contains invalid characters';
    end if;

  elsif ctype = 'set_alert_threshold' then
    if not (p ? 'metric' and p ? 'threshold') then
      raise exception 'set_alert_threshold requires metric and threshold';
    end if;
    if (p->>'metric') not in ('cpu_temp', 'gpu_temp') then
      raise exception 'metric must be cpu_temp or gpu_temp';
    end if;
    if (p->>'threshold')::numeric not between 30 and 120 then
      raise exception 'threshold out of range';
    end if;

  elsif ctype = 'set_all_fans' then
    if not (p ? 'speed_percent') then
      raise exception 'set_all_fans requires speed_percent';
    end if;
    if (p->>'speed_percent')::numeric not between 0 and 100 then
      raise exception 'speed_percent out of range';
    end if;

  elsif ctype = 'set_fan_mode' then
    if not (p ? 'mode') then
      raise exception 'set_fan_mode requires mode';
    end if;
    if (p->>'mode')::int not between 1 and 3 then
      raise exception 'mode must be 1, 2, or 3';
    end if;
  end if;

  -- Clients should never set status; coerce to pending.
  new.status := 'pending';
  new.executed_at := null;

  return new;
end;
$$;

drop trigger if exists trg_validate_command on public.commands;
create trigger trg_validate_command
  before insert on public.commands
  for each row execute function public.validate_command();

-- -----------------------------------------------------------------------
-- Rate limit: max 30 command inserts per user per rolling minute.
-- Uses a small per-user table because Postgres triggers can't see HTTP IPs.
-- -----------------------------------------------------------------------
create table if not exists public.command_rate (
  user_id    uuid not null,
  bucket     timestamptz not null,   -- truncated to minute
  count      int not null default 0,
  primary key (user_id, bucket)
);

alter table public.command_rate enable row level security;
-- No policies on command_rate: only triggers (security definer) touch it.

create or replace function public.enforce_command_rate()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  uid uuid := auth.uid();
  b   timestamptz := date_trunc('minute', now());
  c   int;
begin
  if uid is null then
    -- Service-role / no JWT — skip rate limit.
    return new;
  end if;

  insert into public.command_rate (user_id, bucket, count)
  values (uid, b, 1)
  on conflict (user_id, bucket)
    do update set count = command_rate.count + 1
  returning count into c;

  if c > 30 then
    raise exception 'rate limit exceeded: max 30 commands/minute'
      using errcode = 'too_many_connections';
  end if;

  -- Periodic GC: 1% of inserts purge buckets older than 5 min.
  if random() < 0.01 then
    delete from public.command_rate
      where bucket < now() - interval '5 minutes';
  end if;

  return new;
end;
$$;

drop trigger if exists trg_command_rate on public.commands;
create trigger trg_command_rate
  before insert on public.commands
  for each row execute function public.enforce_command_rate();
