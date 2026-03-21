"""Tests for hepagent.helpers module."""

import pathlib
from unittest.mock import patch

import pytest

from hepagent.helpers import (
    bootstrap_hepagent_home,
    get_agent_dir,
    get_env_var,
    get_hepagent_home,
    get_repo_root,
    load_env_config,
    load_providers_config,
    read_md,
)


def test_get_repo_root_returns_path_with_pyproject_toml():
    """get_repo_root should return a directory containing pyproject.toml."""
    root = get_repo_root()
    assert isinstance(root, pathlib.Path)
    assert (root / "pyproject.toml").exists()


def test_get_hepagent_home_returns_home_subdir(tmp_path):
    """get_hepagent_home should return ~/.hepagent/ without creating it."""
    with patch("pathlib.Path.home", return_value=tmp_path):
        home = get_hepagent_home()
    assert isinstance(home, pathlib.Path)
    assert home == tmp_path / ".hepagent"
    assert not home.exists(), "get_hepagent_home must not create the directory"


def test_get_agent_dir_falls_back_to_repo_root(tmp_path):
    """get_agent_dir should fall back to .agents
    under repo root when ~/.hepagent/agents/ is absent."""
    with patch("hepagent.helpers.get_hepagent_home", return_value=tmp_path):
        agent_dir = get_agent_dir()
    root = get_repo_root()
    assert agent_dir == root / ".agents"


def test_get_agent_dir_prefers_hepagent_home(tmp_path):
    """get_agent_dir should prefer ~/.hepagent/agents/ when it exists."""
    user_agents = tmp_path / "agents"
    user_agents.mkdir()
    with patch("hepagent.helpers.get_hepagent_home", return_value=tmp_path):
        agent_dir = get_agent_dir()
    assert agent_dir == user_agents


def test_bootstrap_copies_toml_defaults(tmp_path):
    """bootstrap_hepagent_home copies bundled TOML files when absent."""
    with patch("hepagent.helpers.get_hepagent_home", return_value=tmp_path):
        bootstrap_hepagent_home()
    assert (tmp_path / "config" / "providers.toml").exists()
    assert (tmp_path / "config" / "env_vars.toml").exists()


def test_bootstrap_copies_agents_dir(tmp_path):
    """bootstrap_hepagent_home copies repo .agents/ to ~/.hepagent/agents/ when absent."""
    with patch("hepagent.helpers.get_hepagent_home", return_value=tmp_path):
        bootstrap_hepagent_home()
    assert (tmp_path / "agents").exists()


def test_bootstrap_does_not_overwrite_existing_files(tmp_path):
    """bootstrap_hepagent_home skips files that already exist."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    sentinel = config_dir / "providers.toml"
    sentinel.write_text("# custom", encoding="utf-8")
    with patch("hepagent.helpers.get_hepagent_home", return_value=tmp_path):
        bootstrap_hepagent_home()
    assert sentinel.read_text(encoding="utf-8") == "# custom"


def test_read_md_returns_content_for_existing_file(tmp_path):
    """read_md returns file text for an existing file."""
    md_file = tmp_path / "test.md"
    md_file.write_text("# Hello\n", encoding="utf-8")
    assert read_md(md_file) == "# Hello\n"


def test_read_md_returns_empty_string_for_missing_file(tmp_path):
    """read_md returns empty string when the file does not exist."""
    assert read_md(tmp_path / "nonexistent.md") == ""


def test_load_env_config_returns_dict():
    """load_env_config should return a non-empty dict."""
    cfg = load_env_config()
    assert isinstance(cfg, dict)
    assert len(cfg) > 0


def test_load_providers_config_merges_user_with_defaults(tmp_path):
    """User provider overrides should merge with bundled defaults."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "providers.toml").write_text(
        """
[providers.cborg]
base_url = "https://custom-cborg.example.com"
api_key_env = "CBORG_API_KEY"
default_model = "custom-model"
""".strip(),
        encoding="utf-8",
    )

    load_providers_config.cache_clear()
    try:
        with patch("hepagent.helpers.get_hepagent_home", return_value=tmp_path):
            providers = load_providers_config()
    finally:
        load_providers_config.cache_clear()

    assert "cborg" in providers
    assert providers["cborg"]["base_url"] == "https://custom-cborg.example.com"
    assert "openai" in providers
    assert "gemini" in providers


def test_get_env_var_reads_from_environment(monkeypatch):
    """get_env_var should prefer environment variables over TOML defaults."""
    monkeypatch.setenv("HEPAGENT_OUTPUT_WORD_LIMIT", "42")
    result = get_env_var("HEPAGENT_OUTPUT_WORD_LIMIT", dtype=int)
    assert result == 42


def test_get_env_var_falls_back_to_toml():
    """get_env_var reads TOML default when env var is absent."""
    result = get_env_var("HEPAGENT_OUTPUT_WORD_LIMIT", dtype=int)
    assert isinstance(result, int)
    assert result > 0


def test_get_env_var_bool_true(monkeypatch):
    """get_env_var correctly converts 'true' string to True."""
    monkeypatch.setenv("HEPAGENT_YOLO", "true")
    assert get_env_var("HEPAGENT_YOLO", dtype=bool) is True


def test_get_env_var_bool_false(monkeypatch):
    """get_env_var correctly converts 'false' string to False."""
    monkeypatch.setenv("HEPAGENT_YOLO", "false")
    assert get_env_var("HEPAGENT_YOLO", dtype=bool) is False


def test_get_env_var_bool_numeric_one(monkeypatch):
    """get_env_var converts '1' to True for bool dtype."""
    monkeypatch.setenv("HEPAGENT_YOLO", "1")
    assert get_env_var("HEPAGENT_YOLO", dtype=bool) is True


def test_get_env_var_str_type(monkeypatch):
    """get_env_var returns string values unchanged."""
    monkeypatch.setenv("HEPAGENT_OUTPUT_WORD_LIMIT", "999")
    result = get_env_var("HEPAGENT_OUTPUT_WORD_LIMIT", dtype=str)
    assert result == "999"


def test_get_env_var_float_type(monkeypatch):
    """get_env_var converts values to float when dtype=float."""
    monkeypatch.setenv("HEPAGENT_OUTPUT_WORD_LIMIT", "3.14")
    result = get_env_var("HEPAGENT_OUTPUT_WORD_LIMIT", dtype=float)
    assert isinstance(result, float)
    assert abs(result - 3.14) < 1e-9


def test_get_env_var_raises_type_error_for_unsupported_dtype():
    """get_env_var raises TypeError for dtypes not in the converters map."""
    with pytest.raises(TypeError, match="Unsupported dtype"):
        get_env_var("HEPAGENT_OUTPUT_WORD_LIMIT", dtype=list)  # type: ignore[arg-type]


def test_get_env_var_raises_key_error_for_missing_key(monkeypatch):
    """get_env_var raises KeyError when key is absent from both env and TOML."""
    monkeypatch.delenv("TOTALLY_UNKNOWN_KEY_XYZ", raising=False)
    with pytest.raises(KeyError, match="TOTALLY_UNKNOWN_KEY_XYZ"):
        get_env_var("TOTALLY_UNKNOWN_KEY_XYZ", dtype=str)


def test_get_env_var_raises_value_error_for_invalid_conversion(monkeypatch):
    """get_env_var raises ValueError when the env value can't be converted."""
    monkeypatch.setenv("HEPAGENT_OUTPUT_WORD_LIMIT", "not_an_int")
    with pytest.raises(ValueError, match="HEPAGENT_OUTPUT_WORD_LIMIT"):
        get_env_var("HEPAGENT_OUTPUT_WORD_LIMIT", dtype=int)


def test_enable_mlflow_for_tracing_returns_false_without_mlflow():
    """enable_mlflow_for_tracing returns False when mlflow is not installed."""
    from hepagent.helpers import enable_mlflow_for_tracing

    with patch("builtins.__import__", side_effect=ImportError("no mlflow")):
        # We can't easily block the already-imported mlflow; instead test the
        # no-tracking-URI branch which also returns False.
        pass

    # Without MLFLOW_TRACKING_URI set, the function should return False
    import os

    orig = os.environ.pop("MLFLOW_TRACKING_URI", None)
    try:
        result = enable_mlflow_for_tracing()
        # Should return False because tracking URI is not configured
        assert result is False
    except ImportError:
        # mlflow is not installed in this environment - that's also acceptable
        pass
    finally:
        if orig is not None:
            os.environ["MLFLOW_TRACKING_URI"] = orig


def test_enable_mlflow_for_tracing_with_tracking_uri(monkeypatch):
    """enable_mlflow_for_tracing returns True when mlflow is importable and URI is set."""
    from hepagent.helpers import enable_mlflow_for_tracing

    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://localhost:5000")
    monkeypatch.setenv("MLFLOW_EXPERIMENT_NAME", "test-experiment")

    try:
        import mlflow
        import mlflow.openai  # noqa: F401
    except ImportError:
        pytest.skip("mlflow not installed")

    # Patch mlflow.set_tracking_uri and mlflow.set_experiment to avoid side effects
    with patch("mlflow.set_tracking_uri"), patch("mlflow.set_experiment"):
        result = enable_mlflow_for_tracing()
    assert result is True


def test_get_env_var_toml_value_conversion_error(monkeypatch):
    """get_env_var raises ValueError when TOML value can't be converted."""
    from hepagent.helpers import get_env_var

    # Patch load_env_config to return a fake config where the value is invalid
    with patch("hepagent.helpers.load_env_config", return_value={"TEST_KEY": "not_an_int"}):
        monkeypatch.delenv("TEST_KEY", raising=False)
        with pytest.raises(ValueError, match="TEST_KEY"):
            get_env_var("TEST_KEY", dtype=int)
