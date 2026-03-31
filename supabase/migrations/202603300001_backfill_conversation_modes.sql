-- Backfill conversation mode defaults and normalize settings.mode

update public.conversations
set mode = 'learn'
where mode is null
   or mode not in ('learn', 'technical', 'socratic');

update public.conversations
set settings = jsonb_set(
  coalesce(settings, '{}'::jsonb),
  '{mode}',
  to_jsonb(mode),
  true
)
where coalesce(settings->>'mode', '') not in ('learn', 'technical', 'socratic');
