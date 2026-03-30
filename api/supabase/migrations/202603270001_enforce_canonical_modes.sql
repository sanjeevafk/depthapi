-- Enforce canonical V2 conversation/history modes only.

update public.history
set mode = case
  when mode = 'technical' then 'technical'
  when mode = 'socratic' then 'socratic'
  else 'learn'
end
where mode is distinct from case
  when mode = 'technical' then 'technical'
  when mode = 'socratic' then 'socratic'
  else 'learn'
end;

update public.conversations
set mode = case
  when mode = 'technical' then 'technical'
  when mode = 'socratic' then 'socratic'
  else 'learn'
end
where mode is distinct from case
  when mode = 'technical' then 'technical'
  when mode = 'socratic' then 'socratic'
  else 'learn'
end;

update public.conversations
set settings = jsonb_set(
  coalesce(settings, '{}'::jsonb),
  '{mode}',
  to_jsonb(
    case
      when coalesce(settings->>'mode', mode) = 'technical' then 'technical'
      when coalesce(settings->>'mode', mode) = 'socratic' then 'socratic'
      else 'learn'
    end
  ),
  true
)
where coalesce(settings->>'mode', '') not in ('', 'learn', 'technical', 'socratic');

alter table public.conversations
  drop constraint if exists conversations_mode_check;

alter table public.conversations
  add constraint conversations_mode_check
  check (mode in ('learn', 'technical', 'socratic'));

alter table public.history
  drop constraint if exists history_mode_check;

alter table public.history
  add constraint history_mode_check
  check (mode in ('learn', 'technical', 'socratic'));
