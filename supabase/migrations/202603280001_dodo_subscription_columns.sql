-- Dodo Payments subscription metadata on public.users (service-role updates via webhooks)
ALTER TABLE public.users
  ADD COLUMN IF NOT EXISTS dodo_customer_id text,
  ADD COLUMN IF NOT EXISTS dodo_subscription_id text,
  ADD COLUMN IF NOT EXISTS subscription_status text,
  ADD COLUMN IF NOT EXISTS current_period_end timestamptz;

COMMENT ON COLUMN public.users.dodo_customer_id IS 'Dodo customer id from webhook payloads';
COMMENT ON COLUMN public.users.dodo_subscription_id IS 'Dodo subscription id from webhook payloads';
COMMENT ON COLUMN public.users.subscription_status IS 'Last known Dodo subscription status string';
COMMENT ON COLUMN public.users.current_period_end IS 'Next billing / period end from Dodo (next_billing_date)';
