create table public.email_logs (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  to_email text not null,
  template text not null,
  status text not null check (status in ('pending', 'sent', 'delivered', 'failed', 'bounced')),
  provider text not null default 'resend',
  provider_message_id text,
  error text,
  user_id uuid references auth.users(id) on delete set null,
  event_type text,
  metadata jsonb
);

create index if not exists email_logs_created_at_idx on public.email_logs (created_at desc);
create index if not exists email_logs_user_id_idx on public.email_logs (user_id);

alter table public.email_logs enable row level security;

create policy "email_logs_select_own"
  on public.email_logs
  for select
  to authenticated
  using (auth.uid() = user_id);

create policy "email_logs_service_role_select"
  on public.email_logs
  for select
  to service_role
  using (true);

create policy "email_logs_service_role_insert"
  on public.email_logs
  for insert
  to service_role
  with check (true);

create policy "email_logs_service_role_update"
  on public.email_logs
  for update
  to service_role
  using (true)
  with check (true);

create policy "email_logs_service_role_delete"
  on public.email_logs
  for delete
  to service_role
  using (true);
