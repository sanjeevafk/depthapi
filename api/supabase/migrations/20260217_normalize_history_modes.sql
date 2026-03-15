-- Normalize history modes to supported values
update "public"."history"
set mode = case
  when mode in ('technical-depth', 'technical_depth') then 'technical'
  else 'learning'
end
where mode not in ('learning', 'technical', 'socratic');
