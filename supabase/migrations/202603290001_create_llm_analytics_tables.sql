-- LLM analytics tables, rollups, and retention policy

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_cron;

-- Raw per-request analytics
CREATE TABLE IF NOT EXISTS llm_requests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  request_id text,
  user_id uuid,
  conversation_id uuid,
  model_alias text,
  model_name text,
  provider text,
  mode text,
  status text,
  tokens_prompt integer,
  tokens_completion integer,
  tokens_total integer,
  estimated_cost_usd numeric(12, 6),
  latency_ms numeric(12, 2),
  model_inference_ms numeric(12, 2),
  stream_duration_ms numeric(12, 2),
  error_type text,
  error_message text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_llm_requests_created_at
  ON llm_requests(created_at);

CREATE INDEX IF NOT EXISTS idx_llm_requests_model_alias
  ON llm_requests(model_alias);

CREATE INDEX IF NOT EXISTS idx_llm_requests_mode
  ON llm_requests(mode);

CREATE INDEX IF NOT EXISTS idx_llm_requests_user_id
  ON llm_requests(user_id);

CREATE INDEX IF NOT EXISTS idx_llm_requests_status
  ON llm_requests(status);

-- Aggregates table (daily/hourly rollups)
CREATE TABLE IF NOT EXISTS llm_aggregates (
  bucket_start timestamptz NOT NULL,
  bucket text NOT NULL,
  model_alias text,
  mode text,
  request_count integer NOT NULL DEFAULT 0,
  total_tokens integer NOT NULL DEFAULT 0,
  total_cost_usd numeric(12, 6) NOT NULL DEFAULT 0,
  avg_latency_ms numeric(12, 2),
  p95_latency_ms numeric(12, 2),
  error_count integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (bucket_start, bucket, model_alias, mode)
);

-- Aggregate refresh helper (daily rollups)
CREATE OR REPLACE FUNCTION llm_refresh_daily_aggregates(target_date date)
RETURNS void
LANGUAGE sql
AS $$
  INSERT INTO llm_aggregates (
    bucket_start,
    bucket,
    model_alias,
    mode,
    request_count,
    total_tokens,
    total_cost_usd,
    avg_latency_ms,
    p95_latency_ms,
    error_count
  )
  SELECT
    date_trunc('day', created_at) AS bucket_start,
    'day' AS bucket,
    model_alias,
    mode,
    COUNT(*) AS request_count,
    COALESCE(SUM(tokens_total), 0) AS total_tokens,
    COALESCE(SUM(estimated_cost_usd), 0) AS total_cost_usd,
    AVG(latency_ms) AS avg_latency_ms,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency_ms,
    SUM(CASE WHEN status IN ('error', 'timed_out', 'aborted') THEN 1 ELSE 0 END) AS error_count
  FROM llm_requests
  WHERE created_at >= target_date
    AND created_at < target_date + INTERVAL '1 day'
  GROUP BY 1, 3, 4
  ON CONFLICT (bucket_start, bucket, model_alias, mode)
  DO UPDATE SET
    request_count = EXCLUDED.request_count,
    total_tokens = EXCLUDED.total_tokens,
    total_cost_usd = EXCLUDED.total_cost_usd,
    avg_latency_ms = EXCLUDED.avg_latency_ms,
    p95_latency_ms = EXCLUDED.p95_latency_ms,
    error_count = EXCLUDED.error_count;
$$;

-- Retention helper
CREATE OR REPLACE FUNCTION llm_delete_old_requests(retention_days integer)
RETURNS void
LANGUAGE sql
AS $$
  DELETE FROM llm_requests
  WHERE created_at < NOW() - (retention_days || ' days')::interval;
$$;

-- Analytics RPC helpers
CREATE OR REPLACE FUNCTION llm_cost_agg(
  start_ts timestamptz,
  end_ts timestamptz,
  bucket text
)
RETURNS TABLE(
  bucket_start timestamptz,
  model_alias text,
  mode text,
  total_cost_usd numeric,
  total_tokens bigint,
  request_count bigint
)
LANGUAGE sql
AS $$
  SELECT
    date_trunc(bucket, created_at) AS bucket_start,
    model_alias,
    mode,
    COALESCE(SUM(estimated_cost_usd), 0) AS total_cost_usd,
    COALESCE(SUM(tokens_total), 0) AS total_tokens,
    COUNT(*) AS request_count
  FROM llm_requests
  WHERE created_at >= start_ts
    AND created_at <= end_ts
  GROUP BY 1, 2, 3
  ORDER BY 1 ASC;
$$;

CREATE OR REPLACE FUNCTION llm_latency_agg(
  start_ts timestamptz,
  end_ts timestamptz,
  bucket text
)
RETURNS TABLE(
  bucket_start timestamptz,
  mode text,
  p50_latency_ms numeric,
  p95_latency_ms numeric,
  p99_latency_ms numeric
)
LANGUAGE sql
AS $$
  SELECT
    date_trunc(bucket, created_at) AS bucket_start,
    mode,
    percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_ms) AS p50_latency_ms,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency_ms,
    percentile_cont(0.99) WITHIN GROUP (ORDER BY latency_ms) AS p99_latency_ms
  FROM llm_requests
  WHERE created_at >= start_ts
    AND created_at <= end_ts
    AND latency_ms IS NOT NULL
  GROUP BY 1, 2
  ORDER BY 1 ASC;
$$;

CREATE OR REPLACE FUNCTION llm_error_agg(
  start_ts timestamptz,
  end_ts timestamptz,
  bucket text
)
RETURNS TABLE(
  bucket_start timestamptz,
  mode text,
  error_count bigint,
  request_count bigint,
  error_rate numeric
)
LANGUAGE sql
AS $$
  SELECT
    date_trunc(bucket, created_at) AS bucket_start,
    mode,
    SUM(CASE WHEN status IN ('error', 'timed_out', 'aborted') THEN 1 ELSE 0 END) AS error_count,
    COUNT(*) AS request_count,
    CASE
      WHEN COUNT(*) = 0 THEN 0
      ELSE ROUND(SUM(CASE WHEN status IN ('error', 'timed_out', 'aborted') THEN 1 ELSE 0 END)::numeric / COUNT(*)::numeric, 6)
    END AS error_rate
  FROM llm_requests
  WHERE created_at >= start_ts
    AND created_at <= end_ts
  GROUP BY 1, 2
  ORDER BY 1 ASC;
$$;

CREATE OR REPLACE FUNCTION llm_top_errors(
  start_ts timestamptz,
  end_ts timestamptz,
  error_limit integer DEFAULT 10
)
RETURNS TABLE(
  error_type text,
  error_message text,
  error_count bigint
)
LANGUAGE sql
AS $$
  SELECT
    COALESCE(error_type, 'unknown') AS error_type,
    COALESCE(error_message, 'unknown') AS error_message,
    COUNT(*) AS error_count
  FROM llm_requests
  WHERE created_at >= start_ts
    AND created_at <= end_ts
    AND status IN ('error', 'timed_out', 'aborted')
  GROUP BY 1, 2
  ORDER BY error_count DESC
  LIMIT error_limit;
$$;

-- Daily cron jobs (03:10 UTC rollup, 03:20 UTC retention)
SELECT cron.schedule(
  'llm_daily_rollup',
  '10 3 * * *',
  $$SELECT llm_refresh_daily_aggregates((NOW() - INTERVAL '1 day')::date);$$
);

SELECT cron.schedule(
  'llm_retention_30d',
  '20 3 * * *',
  $$SELECT llm_delete_old_requests(30);$$
);
