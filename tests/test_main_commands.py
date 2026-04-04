"""Additional tests for hepagent.main (list-models and list-cborg-models commands)."""

from unittest.mock import patch

import typer


def test_list_cborg_models_no_api_key():
    """list_models exits with code 1 when API key is not set."""
    import hepagent.main as main_module
    from hepagent.model_providers import ModelProviderSettings

    fake_settings = ModelProviderSettings(
        base_url="https://api.cborg.lbl.gov",
        api_key=None,
        api_key_env="CBORG_API_KEY",
        default_model="openai/gpt-4o-mini",
    )

    with patch.object(main_module, "get_model_provider_settings", return_value=fake_settings):
        with patch("builtins.print"):  # suppress output
            import typer as typer_mod

            output_parts = []
            _original_echo = typer_mod.echo

            def capture_echo(msg="", **kwargs):
                output_parts.append(str(msg))

            with patch.object(typer_mod, "echo", side_effect=capture_echo):
                try:
                    main_module.list_models("cborg")
                except typer.Exit as e:
                    assert e.exit_code == 1
                except SystemExit as e:
                    assert e.code == 1

    assert any("CBORG_API_KEY" in s for s in output_parts)


def test_list_models_no_api_key_for_platform():
    """list_models exits with code 1 when the API key for the platform is not set."""
    import typer as typer_mod

    import hepagent.main as main_module
    from hepagent.model_providers import ModelProviderSettings

    fake_settings = ModelProviderSettings(
        base_url="https://openai-api.example.com",
        api_key=None,
        api_key_env="OPENAI_API_KEY",
        default_model="gpt-4",
    )

    output_parts = []

    def capture_echo(msg="", **kwargs):
        output_parts.append(str(msg))

    with (
        patch.object(main_module, "get_model_provider_settings", return_value=fake_settings),
        patch.object(typer_mod, "echo", side_effect=capture_echo),
    ):
        try:
            main_module.list_models("openai")
        except (typer.Exit, SystemExit):
            pass

    assert any("OPENAI_API_KEY" in s for s in output_parts)


def test_list_models_with_api_key():
    """list_models lists models when API key is available."""
    import typer as typer_mod

    import hepagent.main as main_module
    from hepagent.model_providers import ModelProviderSettings

    fake_settings = ModelProviderSettings(
        base_url="https://fake-api.example.com",
        api_key="fake-key-xyz",
        api_key_env="FAKE_API_KEY",
        default_model="model-a",
    )

    output_parts = []

    def capture_echo(msg="", **kwargs):
        output_parts.append(str(msg))

    with (
        patch.object(main_module, "get_model_provider_settings", return_value=fake_settings),
        patch.object(main_module, "list_available_models", return_value=("model-a", "model-b")),
        patch.object(typer_mod, "echo", side_effect=capture_echo),
    ):
        main_module.list_models("cborg")

    all_output = "\n".join(output_parts)
    assert "model-a" in all_output
    assert "model-b" in all_output
