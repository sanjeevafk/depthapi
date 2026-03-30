-- Normalize history modes to supported values
update "public"."history"
set mode = case
  when mode in ('technical-depth', 'technical_depth') then 'technical'
  else 'learn'
end
where mode not in ('learn', 'technical', 'socratic');
