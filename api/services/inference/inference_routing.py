"""Stable model alias routing without mode-specific branches."""

def route_model_aliases(model_alias: str | None = None) -> list[str]:
    alias = (model_alias or "technical-primary").strip() or "technical-primary"
    return [alias] if alias == "technical-primary" else [alias, "technical-primary"]
