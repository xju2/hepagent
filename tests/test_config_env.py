"""Tests for hepagent.config.env module."""

from hepagent.config.env import HepAgentEnvConfig


def test_env_config_output_word_limit_default():
    """output_word_limit returns an integer from TOML defaults."""
    cfg = HepAgentEnvConfig()
    limit = cfg.output_word_limit
    assert isinstance(limit, int)
    assert limit > 0


def test_env_config_output_word_limit_env_override(monkeypatch):
    """output_word_limit uses env variable when set."""
    monkeypatch.setenv("HEPAGENT_OUTPUT_WORD_LIMIT", "777")
    cfg = HepAgentEnvConfig()
    assert cfg.output_word_limit == 777


def test_env_config_yolo_default():
    """yolo_mode defaults to False."""
    cfg = HepAgentEnvConfig()
    assert cfg.yolo_mode is False


def test_env_config_yolo_env_override(monkeypatch):
    """yolo_mode returns True when env variable is 'true'."""
    monkeypatch.setenv("HEPAGENT_YOLO", "true")
    cfg = HepAgentEnvConfig()
    assert cfg.yolo_mode is True


def test_env_config_use_mlflow_tracing_default():
    """use_mlflow_tracing defaults to False."""
    cfg = HepAgentEnvConfig()
    assert cfg.use_mlflow_tracing is False


def test_env_config_use_mlflow_tracing_env_override(monkeypatch):
    """use_mlflow_tracing returns True when env variable is '1'."""
    monkeypatch.setenv("HEPAGENT_USE_MLFLOW_Tracing", "1")
    cfg = HepAgentEnvConfig()
    assert cfg.use_mlflow_tracing is True
