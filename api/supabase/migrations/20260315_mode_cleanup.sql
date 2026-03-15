-- Migrate legacy conversation and history mode names to the current set.
update public.history
set mode = case
  when mode in ('technical-depth', 'technical_depth') then 'technical'
  when mode = 'socratic' then 'socratic'
  else 'learning'
end
where mode is null or mode not in ('learning', 'technical', 'socratic');

update public.conversations
set mode = case
  when mode in ('technical-depth', 'technical_depth') then 'technical'
  when mode = 'socratic' then 'socratic'
  else 'learning'
end
where mode is null or mode not in ('learning', 'technical', 'socratic');

update public.conversations
set settings = jsonb_set(
  coalesce(settings, '{}'::jsonb),
  '{mode}',
  to_jsonb(
    case
      when coalesce(settings->>'mode', mode) in ('technical-depth', 'technical_depth') then 'technical'
      when coalesce(settings->>'mode', mode) = 'socratic' then 'socratic'
      else 'learning'
    end
  ),
  true
)
where coalesce(settings->>'mode', '') not in ('', 'learning', 'technical', 'socratic');
