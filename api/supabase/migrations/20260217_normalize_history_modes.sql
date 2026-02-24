-- Normalize history modes to supported values
update public.history
set mode = 'fast'
where mode not in ('fast', 'ensemble');
