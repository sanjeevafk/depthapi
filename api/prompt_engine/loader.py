"""Prompt configuration loading and schema checks."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from string import Formatter

from jsonschema import Draft202012Validator

from api.prompt_engine.models import (
    JsonDict,
    PromptConfigError,
    VALID_DEPTHS,
    VALID_REASONING,
    VALID_STYLES,
    VALID_TASKS,
)

CONFIG_ROOT = Path(__file__).resolve().parents[1] / "prompt_configs"

REQUIRED_FRAGMENT_KEYS = {"name", "template"}


@lru_cache(maxsize=1)
def _fragment_validator() -> Draft202012Validator:
    return Draft202012Validator(_load_json(CONFIG_ROOT / "fragment.schema.json"))


def _load_json(path: Path) -> JsonDict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise PromptConfigError(f"Missing prompt config: {path}") from exc
    except json.JSONDecodeError as exc:
        raise PromptConfigError(f"Malformed JSON config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PromptConfigError(f"Prompt config {path} must contain a JSON object")
    return data


def _validate_fragment(path: Path, data: JsonDict) -> None:
    errors = sorted(_fragment_validator().iter_errors(data), key=lambda error: list(error.path))
    if errors:
        message = "; ".join(error.message for error in errors)
        raise PromptConfigError(f"{path} failed JSON Schema validation: {message}")
    missing = REQUIRED_FRAGMENT_KEYS - data.keys()
    if missing:
        raise PromptConfigError(f"{path} missing required keys: {sorted(missing)}")
    if not isinstance(data["name"], str) or not data["name"]:
        raise PromptConfigError(f"{path} field 'name' must be a non-empty string")
    if not isinstance(data["template"], str) or not data["template"].strip():
        raise PromptConfigError(f"{path} field 'template' must be a non-empty string")
    metadata = data.get("metadata", {})
    if metadata is not None and not isinstance(metadata, dict):
        raise PromptConfigError(f"{path} field 'metadata' must be an object")
    for field_name in ("requires", "injectors"):
        value = data.get(field_name, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise PromptConfigError(f"{path} field '{field_name}' must be a string list")


def _load_fragment(kind: str, name: str) -> JsonDict:
    path = CONFIG_ROOT / kind / f"{name}.json"
    data = _load_json(path)
    _validate_fragment(path, data)
    return data | {"_path": str(path.relative_to(CONFIG_ROOT))}


def _template_variables(template: str) -> set[str]:
    variables: set[str] = set()
    for _, field_name, _, _ in Formatter().parse(template):
        if field_name:
            variables.add(field_name.split(".", 1)[0].split("[", 1)[0])
    return variables


def validate_template_variables(template: str, available: set[str], source: str) -> None:
    missing = _template_variables(template) - available
    if missing:
        raise PromptConfigError(
            f"{source} references unsupported template variables: {sorted(missing)}"
        )


@lru_cache(maxsize=1)
def load_prompt_config() -> JsonDict:
    schema = _load_json(CONFIG_ROOT / "schema.json")
    layers = {
        name: _load_fragment("layers", name)
        for name in (
            "core_identity",
            "safety_policy",
            "pedagogy_rules",
            "formatting_rules",
        )
    }
    config: JsonDict = {
        "schema": schema,
        "layers": layers,
        "depths": {name: _load_fragment("depths", name) for name in sorted(VALID_DEPTHS)},
        "tasks": {name: _load_fragment("tasks", name) for name in sorted(VALID_TASKS)},
        "reasoning": {
            name: _load_fragment("reasoning", name) for name in sorted(VALID_REASONING)
        },
        "styles": {name: _load_fragment("styles", name) for name in sorted(VALID_STYLES)},
        "injectors": {
            name: _load_fragment("injectors", name)
            for name in ("conversation_context", "search_context", "quote", "diagram")
        },
    }
    _validate_loaded_config(config)
    return config


def _validate_loaded_config(config: JsonDict) -> None:
    available_vars = {
        "topic",
        "conversation_context",
        "search_context",
        "quote_text",
        "diagram_instruction",
    }
    for group_name in ("layers", "depths", "tasks", "reasoning", "styles", "injectors"):
        for name, fragment in config[group_name].items():
            validate_template_variables(
                fragment["template"], available_vars, f"{group_name}/{name}"
            )

    variables_pattern = re.compile(r"{[^{}]+}")
    for group_name in ("layers", "depths", "tasks", "reasoning", "styles", "injectors"):
        for name, fragment in config[group_name].items():
            try:
                fragment["template"].format(
                    topic="topic",
                    conversation_context="context",
                    search_context="search",
                    quote_text="quote",
                    diagram_instruction="diagram",
                )
            except Exception as exc:
                raise PromptConfigError(
                    f"{group_name}/{name} cannot be formatted despite variables "
                    f"{variables_pattern.findall(fragment['template'])}: {exc}"
                ) from exc
