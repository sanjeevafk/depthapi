INSERT INTO api_keys (key_hash, plan, monthly_token_budget) VALUES (encode(digest('sk-depth-dev-local-0000000000000000', 'sha256'), 'hex'), 'enterprise', 0) ON CONFLICT (key_hash) DO NOTHING;
