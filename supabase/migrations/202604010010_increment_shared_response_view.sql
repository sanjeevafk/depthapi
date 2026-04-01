-- Atomic view count increment for shared responses

CREATE OR REPLACE FUNCTION increment_shared_response_view(share_id uuid)
RETURNS void
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  UPDATE shared_responses
  SET view_count = view_count + 1,
      last_viewed_at = NOW()
  WHERE id = share_id;
$$;
