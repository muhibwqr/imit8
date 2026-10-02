import json

from imit8 import config
from imit8.config import Config


def use_home(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "HOME_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config, "_dotenv", lambda: {})  # keep a real .env out of tests
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)


def test_save_api_key_persists_and_is_loaded(monkeypatch, tmp_path):
    use_home(monkeypatch, tmp_path)
    Config(api_key="sk-or-test").save_api_key()
    assert Config.load().api_key == "sk-or-test"
    mode = config.CONFIG_PATH.stat().st_mode & 0o777
    assert mode == 0o600


def test_save_preserves_a_stored_key(monkeypatch, tmp_path):
    use_home(monkeypatch, tmp_path)
    Config(api_key="sk-or-test").save_api_key()
    Config.load().save()  # saving other settings must not drop the key
    assert json.loads(config.CONFIG_PATH.read_text())["api_key"] == "sk-or-test"


def test_dotenv_supplies_key_when_env_is_absent(monkeypatch, tmp_path):
    use_home(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "_dotenv", lambda: {"OPENROUTER_API_KEY": "sk-or-dotenv"})
    assert Config.load().api_key == "sk-or-dotenv"


def test_env_var_overrides_stored_key(monkeypatch, tmp_path):
    use_home(monkeypatch, tmp_path)
    Config(api_key="sk-or-file").save_api_key()
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-env")
    assert Config.load().api_key == "sk-or-env"
