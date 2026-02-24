import pytest

import services.ensemble as ensemble_module


@pytest.mark.asyncio
async def test_ensemble_fast_mode(monkeypatch):
    async def fake_generate_explanation(_topic, _level, _model, **_kwargs):
        return "fast"

    monkeypatch.setattr(ensemble_module, "generate_explanation", fake_generate_explanation)
    result = await ensemble_module.ensemble_generate("topic", "eli5", use_premium=False, mode="fast")
    assert result == "fast"


@pytest.mark.asyncio
async def test_ensemble_judges_responses(monkeypatch):
    monkeypatch.setattr(ensemble_module, "FREE_MODELS", ["m1", "m2"])

    async def fake_generate_explanation(_topic, _level, model, **_kwargs):
        return "resp1" if model == "m1" else "resp2"

    async def fake_judge(_topic, responses):
        assert responses == ["resp1", "resp2"]
        return "resp2"

    monkeypatch.setattr(ensemble_module, "generate_explanation", fake_generate_explanation)
    monkeypatch.setattr(ensemble_module, "judge_responses", fake_judge)

    result = await ensemble_module.ensemble_generate("topic", "eli5", use_premium=False, mode="ensemble")
    assert result == "resp2"


@pytest.mark.asyncio
async def test_ensemble_all_models_fail(monkeypatch):
    monkeypatch.setattr(ensemble_module, "FREE_MODELS", ["m1", "m2"])

    async def fake_generate_explanation(_topic, _level, _model, **_kwargs):
        raise RuntimeError("fail")

    monkeypatch.setattr(ensemble_module, "generate_explanation", fake_generate_explanation)

    with pytest.raises(RuntimeError):
        await ensemble_module.ensemble_generate("topic", "eli5", use_premium=False, mode="ensemble")
