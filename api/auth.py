"""Database dependency helpers."""
from api.adapters.pg_adapter import get_pool

def get_pg_pool():
    return get_pool()
