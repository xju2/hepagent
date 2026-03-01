"""Tests for hepagent.config.first_run."""

import os
import pathlib

import hepagent.config.first_run as first_run

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_config_file(monkeypatch, tmp_path: pathlib.Path):
    """Redirect the module-level config paths to a temp directory."""
    config_dir = tmp_path / ".config" / "hepagent"
    config_file = config_dir / "config.toml"
    monkeypatch.setattr(first_run, "_CONFIG_DIR", config_dir)
    monkeypatch.setattr(first_run, "_CONFIG_FILE", config_file)
    return config_dir, config_file


# ---------------------------------------------------------------------------
# is_first_run
# ---------------------------------------------------------------------------


def test_is_first_run_returns_true_when_no_config(monkeypatch, tmp_path):
    _patch_config_file(monkeypatch, tmp_path)
    assert first_run.is_first_run() is True


def test_is_first_run_returns_false_when_config_exists(monkeypatch, tmp_path):
    config_dir, config_file = _patch_config_file(monkeypatch, tmp_path)
    config_dir.mkdir(parents=True)
    config_file.write_text('default_platform = "cborg"\n', encoding="utf-8")
    assert first_run.is_first_run() is False


# ---------------------------------------------------------------------------
# _to_toml / save_user_config / load_user_config round-trip
# ---------------------------------------------------------------------------


def test_to_toml_flat():
    result = first_run._to_toml({"default_platform": "openai"})
    assert 'default_platform = "openai"' in result


def test_to_toml_escapes_special_chars():
    result = first_run._to_toml({"k": 'val"with\\special'})
    assert r'val\"with\\special' in result


    result = first_run._to_toml({"api_keys": {"OPENAI_API_KEY": "sk-test"}})
    assert "[api_keys]" in result
    assert 'OPENAI_API_KEY = "sk-test"' in result


def test_save_and_load_roundtrip(monkeypatch, tmp_path):
    _patch_config_file(monkeypatch, tmp_path)
    config = {"default_platform": "cborg", "api_keys": {"CBORG_API_KEY": "abc123"}}
    first_run.save_user_config(config)
    loaded = first_run.load_user_config()
    assert loaded["default_platform"] == "cborg"
    assert loaded["api_keys"]["CBORG_API_KEY"] == "abc123"


# ---------------------------------------------------------------------------
# run_setup_wizard
# ---------------------------------------------------------------------------


def test_run_setup_wizard_saves_config(monkeypatch, tmp_path):
    _patch_config_file(monkeypatch, tmp_path)

    # Simulate user inputs: choose platform "1" (first provider), then enter key
    inputs = iter(["1", "my-secret-key"])
    monkeypatch.setattr("click.prompt", lambda *args, **kwargs: next(inputs))
    monkeypatch.setattr("click.echo", lambda *args, **kwargs: None)

    first_run.run_setup_wizard()

    config = first_run.load_user_config()
    assert "default_platform" in config
    assert "api_keys" in config
    # The key must be stored
    api_keys = config["api_keys"]
    assert any(v == "my-secret-key" for v in api_keys.values())


def test_run_setup_wizard_uses_existing_env_key(monkeypatch, tmp_path):
    _patch_config_file(monkeypatch, tmp_path)

    # Pre-set the env var for the first provider
    from hepagent.helpers import load_providers_config

    providers = load_providers_config()
    first_provider = list(providers.keys())[0]
    api_key_env = providers[first_provider]["api_key_env"]
    monkeypatch.setenv(api_key_env, "env-provided-key")

    # User only needs to choose the platform (no key prompt)
    inputs = iter(["1"])
    monkeypatch.setattr("click.prompt", lambda *args, **kwargs: next(inputs))
    monkeypatch.setattr("click.echo", lambda *args, **kwargs: None)

    first_run.run_setup_wizard()

    config = first_run.load_user_config()
    assert config["api_keys"][api_key_env] == "env-provided-key"


# ---------------------------------------------------------------------------
# maybe_run_setup_wizard
# ---------------------------------------------------------------------------


def test_maybe_run_setup_wizard_calls_wizard_on_first_run(monkeypatch, tmp_path):
    _patch_config_file(monkeypatch, tmp_path)
    called = []
    monkeypatch.setattr(first_run, "run_setup_wizard", lambda: called.append(True))
    first_run.maybe_run_setup_wizard()
    assert called == [True]


def test_maybe_run_setup_wizard_loads_keys_on_subsequent_run(monkeypatch, tmp_path):
    _patch_config_file(monkeypatch, tmp_path)
    config = {"default_platform": "cborg", "api_keys": {"CBORG_API_KEY": "saved-key"}}
    first_run.save_user_config(config)

    # Make sure the env var is not already set
    monkeypatch.delenv("CBORG_API_KEY", raising=False)

    first_run.maybe_run_setup_wizard()

    assert os.environ.get("CBORG_API_KEY") == "saved-key"
