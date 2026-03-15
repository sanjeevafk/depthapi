-- Normalize history modes to supported values
update public.history
set mode = case
  when mode in ('technical-depth', 'technical_depth') then 'technical'
  when mode = 'socratic' then 'socratic'
  else 'learning'
end
where mode not in ('learning', 'technical', 'socratic');
