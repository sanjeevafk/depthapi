import pytest

import services.ensemble as ensemble_module


@pytest.mark.asyncio
async def test_ensemble_learning_mode_always_uses_judge(monkeypatch):
    async def fake_generate_explanation(_topic, _level, _model, **_kwargs):
        return "candidate"

    class DummyChoice:
        def __init__(self, content: str):
            self.message = type("Msg", (), {"content": content})

    class DummyResponse:
        def __init__(self, content: str):
            self.choices = [DummyChoice(content)]

    async def fake_create_chat_completion(model, messages, **_kwargs):
        assert model == "judge"
        assert messages
        return DummyResponse('{"final_response":"judged"}')

    monkeypatch.setattr(ensemble_module, "generate_explanation", fake_generate_explanation)
    monkeypatch.setattr(ensemble_module, "create_chat_completion", fake_create_chat_completion)
    result = await ensemble_module.ensemble_generate("topic", "eli5", use_premium=False, mode="learning")
    assert result == "judged"


@pytest.mark.asyncio
async def test_ensemble_rejects_non_learning_modes():
    with pytest.raises(ValueError):
        await ensemble_module.ensemble_generate("topic", "eli5", use_premium=False, mode="technical")


@pytest.mark.asyncio
async def test_ensemble_all_models_fail(monkeypatch):
    monkeypatch.setattr(ensemble_module, "LEARNING_CANDIDATE_MODELS", ["m1", "m2"])

    async def fake_generate_explanation(_topic, _level, _model, **_kwargs):
        raise RuntimeError("fail")

    monkeypatch.setattr(ensemble_module, "generate_explanation", fake_generate_explanation)

    with pytest.raises(RuntimeError):
        await ensemble_module.ensemble_generate("topic", "eli5", use_premium=False, mode="learning")
