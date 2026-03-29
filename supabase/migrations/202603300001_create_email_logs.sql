create table public.email_logs (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  to_email text not null,
  template text not null,
  status text not null,
  provider text not null default 'resend',
  provider_message_id text,
  error text,
  user_id uuid,
  event_type text,
  metadata jsonb
);

create index if not exists email_logs_created_at_idx on public.email_logs (created_at desc);
create index if not exists email_logs_user_id_idx on public.email_logs (user_id);

alter table public.email_logs enable row level security;
