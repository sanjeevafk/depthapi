-- Enforce unique share tokens to support safe retry-on-conflict.

CREATE UNIQUE INDEX IF NOT EXISTS shared_responses_share_token_idx
    ON shared_responses (share_token);
