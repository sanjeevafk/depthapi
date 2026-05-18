BEGIN;

CREATE OR REPLACE FUNCTION public.hybrid_search_v5(
  query_text text,
  query_embedding vector,
  target_api_key_id uuid,
  query_mode text DEFAULT 'conceptual'::text,
  candidate_pool_size integer DEFAULT 100,
  final_count integer DEFAULT 10,
  min_similarity double precision DEFAULT 0.65,
  target_collection_id uuid DEFAULT NULL::uuid
)
RETURNS TABLE(
  chunk_id uuid,
  document_id uuid,
  content text,
  metadata jsonb,
  filename text,
  source_url text,
  chunk_order integer,
  vector_similarity double precision,
  rrf_score double precision,
  match_source text
)
LANGUAGE plpgsql
SET statement_timeout TO '60s'
AS $function$
DECLARE
  k_dense          INT;
  k_sparse_simple  INT;
  k_sparse_english INT;
  cleaned_query    TEXT;
  cleaned_simple   TEXT;
  v_collection_ids UUID[];
BEGIN
  SELECT array_agg(kc.id) INTO v_collection_ids
  FROM public.knowledge_collections kc
  WHERE kc.api_key_id = target_api_key_id
    AND (target_collection_id IS NULL OR kc.id = target_collection_id)
    AND kc.deleted_at IS NULL;

  IF v_collection_ids IS NULL OR array_length(v_collection_ids, 1) = 0 THEN
    RETURN;
  END IF;

  cleaned_query := regexp_replace(COALESCE(query_text, ''), '[^[:alnum:][:space:]]', ' ', 'g');
  cleaned_query := trim(regexp_replace(cleaned_query, '\s+', ' ', 'g'));
  cleaned_simple := regexp_replace(cleaned_query, '\s+', ' & ', 'g');

  IF query_mode = 'code' OR query_mode = 'technical' THEN
    k_dense          := 100;
    k_sparse_simple  := 5;
    k_sparse_english := 20;
  ELSE
    k_dense          := 5;
    k_sparse_simple  := 100;
    k_sparse_english := 60;
  END IF;

  RETURN QUERY
  WITH vector_candidates AS (
    SELECT
      kc.id AS candidate_id,
      (1 - (kc.embedding <=> query_embedding))::FLOAT AS similarity,
      ROW_NUMBER() OVER (ORDER BY kc.embedding <=> query_embedding, kc.id) AS rank
    FROM public.knowledge_chunks kc
    JOIN public.knowledge_documents kd ON kc.document_id = kd.id
    WHERE kd.collection_id = ANY(v_collection_ids)
      AND kc.embedding IS NOT NULL
      AND kc.deleted_at IS NULL
      AND kd.deleted_at IS NULL
    ORDER BY kc.embedding <=> query_embedding
    LIMIT candidate_pool_size
  ),
  vector_ranks AS (
    SELECT *
    FROM vector_candidates
    WHERE similarity >= min_similarity
  ),
  fts_english_ranks AS (
    SELECT
      kc.id AS candidate_id,
      ROW_NUMBER() OVER (
        ORDER BY ts_rank_cd(kc.fts_tokens, websearch_to_tsquery('english', cleaned_query)) DESC, kc.id
      ) AS rank
    FROM public.knowledge_chunks kc
    JOIN public.knowledge_documents kd ON kc.document_id = kd.id
    WHERE kd.collection_id = ANY(v_collection_ids)
      AND cleaned_query <> ''
      AND kc.fts_tokens @@ websearch_to_tsquery('english', cleaned_query)
      AND kc.deleted_at IS NULL
      AND kd.deleted_at IS NULL
    ORDER BY ts_rank_cd(kc.fts_tokens, websearch_to_tsquery('english', cleaned_query)) DESC
    LIMIT candidate_pool_size
  ),
  fts_simple_ranks AS (
    SELECT
      kc.id AS candidate_id,
      ROW_NUMBER() OVER (
        ORDER BY ts_rank_cd(kc.fts_tokens_simple, to_tsquery('simple', cleaned_simple)) DESC, kc.id
      ) AS rank
    FROM public.knowledge_chunks kc
    JOIN public.knowledge_documents kd ON kc.document_id = kd.id
    WHERE kd.collection_id = ANY(v_collection_ids)
      AND kc.fts_tokens_simple IS NOT NULL
      AND cleaned_simple <> ''
      AND kc.fts_tokens_simple @@ to_tsquery('simple', cleaned_simple)
      AND kc.deleted_at IS NULL
      AND kd.deleted_at IS NULL
    ORDER BY ts_rank_cd(kc.fts_tokens_simple, to_tsquery('simple', cleaned_simple)) DESC
    LIMIT candidate_pool_size
  ),
  all_candidates AS (
    SELECT vr.candidate_id
    FROM vector_ranks vr
    UNION
    SELECT fer.candidate_id
    FROM fts_english_ranks fer
    UNION
    SELECT fsr.candidate_id
    FROM fts_simple_ranks fsr
  ),
  rrf_scores AS (
    SELECT
      ac.candidate_id,
      (
        COALESCE(1.0 / (k_dense          + vr.rank), 0.0) +
        COALESCE(1.0 / (k_sparse_english + fer.rank), 0.0) +
        COALESCE(1.0 / (k_sparse_simple  + fsr.rank), 0.0)
      )::FLOAT AS score,
      COALESCE(vr.similarity, 0.0) AS vsim,
      CASE
        WHEN vr.candidate_id IS NOT NULL AND (fer.candidate_id IS NOT NULL OR fsr.candidate_id IS NOT NULL) THEN 'hybrid'
        WHEN vr.candidate_id IS NOT NULL THEN 'dense'
        WHEN fsr.candidate_id IS NOT NULL THEN 'sparse_simple'
        ELSE 'sparse_english'
      END AS match_src
    FROM all_candidates ac
    LEFT JOIN vector_ranks vr ON ac.candidate_id = vr.candidate_id
    LEFT JOIN fts_english_ranks fer ON ac.candidate_id = fer.candidate_id
    LEFT JOIN fts_simple_ranks fsr ON ac.candidate_id = fsr.candidate_id
  )
  SELECT
    kc.id AS chunk_id,
    kc.document_id,
    kc.content,
    kc.metadata,
    kd.filename,
    kd.source_url,
    kc.chunk_order,
    rs.vsim,
    rs.score,
    rs.match_src
  FROM rrf_scores rs
  JOIN public.knowledge_chunks kc ON rs.candidate_id = kc.id
  JOIN public.knowledge_documents kd ON kc.document_id = kd.id
  ORDER BY rs.score DESC
  LIMIT final_count;
END;
$function$;

CREATE OR REPLACE FUNCTION public.hybrid_search_trusted_v5(
  query_text text,
  query_embedding vector,
  query_mode text DEFAULT 'conceptual'::text,
  candidate_pool_size integer DEFAULT 100,
  final_count integer DEFAULT 10,
  min_similarity double precision DEFAULT 0.65
)
RETURNS TABLE(
  chunk_id uuid,
  document_id uuid,
  content text,
  metadata jsonb,
  filename text,
  source_url text,
  chunk_order integer,
  vector_similarity double precision,
  rrf_score double precision,
  match_source text
)
LANGUAGE plpgsql
SET statement_timeout TO '60s'
AS $function$
DECLARE
  k_dense          INT;
  k_sparse_simple  INT;
  k_sparse_english INT;
  cleaned_query    TEXT;
  cleaned_simple   TEXT;
BEGIN
  cleaned_query := regexp_replace(COALESCE(query_text, ''), '[^[:alnum:][:space:]]', ' ', 'g');
  cleaned_query := trim(regexp_replace(cleaned_query, '\s+', ' ', 'g'));
  cleaned_simple := regexp_replace(cleaned_query, '\s+', ' & ', 'g');

  IF query_mode = 'code' OR query_mode = 'technical' THEN
    k_dense          := 100;
    k_sparse_simple  := 5;
    k_sparse_english := 20;
  ELSE
    k_dense          := 5;
    k_sparse_simple  := 100;
    k_sparse_english := 60;
  END IF;

  RETURN QUERY
  WITH vector_candidates AS (
    SELECT
      kc.id AS candidate_id,
      (1 - (kc.embedding <=> query_embedding))::FLOAT AS similarity,
      ROW_NUMBER() OVER (ORDER BY kc.embedding <=> query_embedding, kc.id) AS rank
    FROM public.knowledge_chunks kc
    WHERE kc.embedding IS NOT NULL
      AND kc.deleted_at IS NULL
    ORDER BY kc.embedding <=> query_embedding
    LIMIT candidate_pool_size
  ),
  vector_ranks AS (
    SELECT *
    FROM vector_candidates
    WHERE similarity >= min_similarity
  ),
  fts_english_ranks AS (
    SELECT
      kc.id AS candidate_id,
      ROW_NUMBER() OVER (
        ORDER BY ts_rank_cd(kc.fts_tokens, websearch_to_tsquery('english', cleaned_query)) DESC, kc.id
      ) AS rank
    FROM public.knowledge_chunks kc
    WHERE cleaned_query <> ''
      AND kc.fts_tokens @@ websearch_to_tsquery('english', cleaned_query)
      AND kc.deleted_at IS NULL
    ORDER BY ts_rank_cd(kc.fts_tokens, websearch_to_tsquery('english', cleaned_query)) DESC
    LIMIT candidate_pool_size
  ),
  fts_simple_ranks AS (
    SELECT
      kc.id AS candidate_id,
      ROW_NUMBER() OVER (
        ORDER BY ts_rank_cd(kc.fts_tokens_simple, to_tsquery('simple', cleaned_simple)) DESC, kc.id
      ) AS rank
    FROM public.knowledge_chunks kc
    WHERE kc.fts_tokens_simple IS NOT NULL
      AND cleaned_simple <> ''
      AND kc.fts_tokens_simple @@ to_tsquery('simple', cleaned_simple)
      AND kc.deleted_at IS NULL
    ORDER BY ts_rank_cd(kc.fts_tokens_simple, to_tsquery('simple', cleaned_simple)) DESC
    LIMIT candidate_pool_size
  ),
  all_candidates AS (
    SELECT vr.candidate_id
    FROM vector_ranks vr
    UNION
    SELECT fer.candidate_id
    FROM fts_english_ranks fer
    UNION
    SELECT fsr.candidate_id
    FROM fts_simple_ranks fsr
  ),
  rrf_scores AS (
    SELECT
      ac.candidate_id,
      (
        COALESCE(1.0 / (k_dense          + vr.rank), 0.0) +
        COALESCE(1.0 / (k_sparse_english + fer.rank), 0.0) +
        COALESCE(1.0 / (k_sparse_simple  + fsr.rank), 0.0)
      )::FLOAT AS score,
      COALESCE(vr.similarity, 0.0) AS vsim,
      CASE
        WHEN vr.candidate_id IS NOT NULL AND (fer.candidate_id IS NOT NULL OR fsr.candidate_id IS NOT NULL) THEN 'hybrid'
        WHEN vr.candidate_id IS NOT NULL THEN 'dense'
        WHEN fsr.candidate_id IS NOT NULL THEN 'sparse_simple'
        ELSE 'sparse_english'
      END AS match_src
    FROM all_candidates ac
    LEFT JOIN vector_ranks vr ON ac.candidate_id = vr.candidate_id
    LEFT JOIN fts_english_ranks fer ON ac.candidate_id = fer.candidate_id
    LEFT JOIN fts_simple_ranks fsr ON ac.candidate_id = fsr.candidate_id
  )
  SELECT
    kc.id AS chunk_id,
    kc.document_id,
    kc.content,
    kc.metadata,
    kd.filename,
    kd.source_url,
    kc.chunk_order,
    rs.vsim,
    rs.score,
    rs.match_src
  FROM rrf_scores rs
  JOIN public.knowledge_chunks kc ON rs.candidate_id = kc.id
  JOIN public.knowledge_documents kd ON kc.document_id = kd.id
  ORDER BY rs.score DESC
  LIMIT final_count;
END;
$function$;

COMMIT;
