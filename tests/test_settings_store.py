import stat

import pytest
from fastapi.testclient import TestClient

from clipstudio import highlights
from clipstudio.app import app, provider_settings
from clipstudio.models import AIProvider, ProviderSettings
from clipstudio.settings_store import ProviderSettingsStore

client = TestClient(app)
SECRET = "sk-ant-test-secret-9876"


@pytest.fixture(autouse=True)
def clean_store(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    for provider in AIProvider:
        if provider not in (AIProvider.AUTO, AIProvider.LOCAL):
            provider_settings.forget(provider)
    yield


def _provider(data, provider_id):
    return next(p for p in data["providers"] if p["id"] == provider_id)


def test_settings_survive_a_new_store_on_the_same_file(tmp_path):
    path = tmp_path / "s.db"
    ProviderSettingsStore(path).save(AIProvider.CLAUDE, ProviderSettings(api_key=SECRET))
    assert ProviderSettingsStore(path).all()[AIProvider.CLAUDE].api_key == SECRET
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_saving_one_field_keeps_the_others(tmp_path):
    store = ProviderSettingsStore(tmp_path / "s.db")
    store.save(AIProvider.OPENAI, ProviderSettings(api_key="k-123456789", model="llama3.1", base_url="http://localhost:11434/v1"))
    store.save(AIProvider.OPENAI, ProviderSettings(model="qwen2.5"))
    assert store.all()[AIProvider.OPENAI] == ProviderSettings(
        api_key="k-123456789", model="qwen2.5", base_url="http://localhost:11434/v1"
    )
    store.forget(AIProvider.OPENAI)
    assert store.all() == {}


def test_saved_key_is_reported_but_never_returned():
    res = client.put("/api/ai/providers/claude", json={"api_key": f"  {SECRET}  "})
    assert res.status_code == 200
    assert SECRET not in res.text
    claude = _provider(res.json(), "claude")
    assert claude["has_server_key"] and claude["has_saved_settings"]
    assert claude["saved_key_hint"] == "…9876"
    assert res.json()["auto"] == "claude"
    assert SECRET not in client.get("/api/ai/providers").text


def test_forget_removes_the_saved_key():
    client.put("/api/ai/providers/gemini", json={"api_key": SECRET})
    res = client.delete("/api/ai/providers/gemini")
    gemini = _provider(res.json(), "gemini")
    assert not gemini["has_server_key"] and gemini["saved_key_hint"] is None
    assert res.json()["auto"] == "local"


def test_auto_and_local_have_nothing_to_save():
    assert client.put("/api/ai/providers/local", json={"api_key": SECRET}).status_code == 400
    assert client.put("/api/ai/providers/auto", json={"api_key": SECRET}).status_code == 400


def test_openai_accepts_a_key_even_though_it_is_optional():
    openai = _provider(client.get("/api/ai/providers").json(), "openai")
    assert openai["accepts_key"] and not openai["needs_key"]
    local = _provider(client.get("/api/ai/providers").json(), "local")
    assert not local["accepts_key"]


def test_request_value_beats_saved_value_beats_env(monkeypatch):
    seen = {}

    def fake_call(request, api_key, model, base_url):
        seen.update(api_key=api_key, model=model, base_url=base_url)
        return highlights.HighlightPlan(highlights=[
            highlights.LineSpan(start_line=0, end_line=0, title="t", reason="r", score=0.5)
        ])

    spec = highlights.PROVIDERS[AIProvider.OPENAI]
    monkeypatch.setitem(highlights.PROVIDERS, AIProvider.OPENAI, highlights.ProviderSpec(**{**spec.__dict__, "call": fake_call}))
    monkeypatch.setenv("OPENAI_API_KEY", "env-key-0000")
    monkeypatch.setenv("OPENAI_MODEL", "env-model")
    client.put("/api/ai/providers/openai", json={"api_key": "saved-key-1111", "model": "saved-model"})
    captions = [{"start": 0.0, "end": 6.0, "text": "hello there"}]

    client.post("/api/ai-highlights", json={"captions": captions, "provider": "openai"})
    assert seen == {"api_key": "saved-key-1111", "model": "saved-model", "base_url": "https://api.openai.com/v1"}

    client.post("/api/ai-highlights", json={"captions": captions, "provider": "openai", "model": "typed-model"})
    assert seen["model"] == "typed-model" and seen["api_key"] == "saved-key-1111"
