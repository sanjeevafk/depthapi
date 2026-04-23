from pydantic import SecretStr

from api.config import Settings


def test_provider_keys_are_secretstr_and_trimmed():
    settings = Settings(
        groq_api_key=SecretStr("  gsk-test  "),
        cerebras_api_key=SecretStr(" cs-test "),
        gemini_api_key=SecretStr(" gm-test "),
        openrouter_api_key=SecretStr(" or-test "),
    )

    assert isinstance(settings.groq_api_key, SecretStr)
    assert isinstance(settings.cerebras_api_key, SecretStr)
    assert isinstance(settings.gemini_api_key, SecretStr)
    assert isinstance(settings.openrouter_api_key, SecretStr)
    assert settings.groq_api_key.get_secret_value() == "gsk-test"
    assert settings.cerebras_api_key.get_secret_value() == "cs-test"
    assert settings.gemini_api_key.get_secret_value() == "gm-test"
    assert settings.openrouter_api_key.get_secret_value() == "or-test"


def test_provider_key_masking_in_repr_and_model_dump():
    raw_key = "super-secret-key"
    settings = Settings(groq_api_key=SecretStr(raw_key))

    assert raw_key not in repr(settings)
    dumped = settings.model_dump()
    assert isinstance(dumped["groq_api_key"], SecretStr)
    assert dumped["groq_api_key"].get_secret_value() == raw_key
    assert raw_key not in str(dumped["groq_api_key"])


def test_provider_key_validator_accepts_secretstr_input():
    settings = Settings(groq_api_key=SecretStr("  secret-from-secretstr  "))
    assert settings.groq_api_key.get_secret_value() == "secret-from-secretstr"
