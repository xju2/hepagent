import os
import pathlib
from importlib.resources.abc import Traversable

from hepagent.utils.config_loader import env_config


def _copy_traversable(src: Traversable, dst: pathlib.Path) -> None:
    """Recursively copy a Traversable resource tree to a filesystem path."""
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        dest_path = dst / item.name
        if item.is_dir():
            _copy_traversable(item, dest_path)
        else:
            dest_path.write_bytes(item.read_bytes())


def read_md(path: pathlib.Path) -> str:
    """Safely reads a markdown file, returning an empty string if missing."""
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _enable_amsc_x_api_key() -> bool:
    import mlflow.utils.rest_utils as rest_utils

    api_key = env_config.getenv("AMSC_MLFLOW_API_KEY", str)
    if not api_key:
        return False

    original_http_request = rest_utils.http_request

    def patched(host_creds, endpoint, method, *args, **kwargs):
        headers = dict(kwargs.get("extra_headers") or {})
        if kwargs.get("headers") is not None:
            headers.update(dict(kwargs["headers"]))
        headers["X-Api-Key"] = api_key
        kwargs["extra_headers"] = headers
        kwargs.pop("headers", None)
        return original_http_request(host_creds, endpoint, method, *args, **kwargs)

    rest_utils.http_request = patched
    return True


def enable_mlflow_for_tracing() -> bool:
    """Enable and configure MLflow-based tracing if MLflow is available.

    This function:
    - Imports MLflow and required urllib3 warning classes.
    - Monkey-patches MLflow's HTTP client to inject the AMSC API key.
    - Sets environment variables needed for insecure TLS connections.
    - Disables insecure TLS warnings from urllib3.
    - Configures the MLflow tracking URI and experiment for log tracing.

    Returns:
        True if MLflow is successfully imported and tracing is configured;
        False if MLflow is not installed (ImportError).
    """
    try:
        import mlflow
        import urllib3
        from urllib3.exceptions import InsecureRequestWarning

        tracking_uri = env_config.getenv("MLFLOW_TRACKING_URI", str)
        if not tracking_uri:
            print("MLFLOW_TRACKING_URI not set; skipping MLflow tracing setup.")
            return False

        if "american-science-cloud.org" in tracking_uri:
            if not _enable_amsc_x_api_key():
                print(
                    "AMSC_MLFLOW_API_KEY not set; "
                    "MLflow tracing to AMSC will fail due to authentication issues."
                )
                return False
            os.environ["MLFLOW_TRACKING_INSECURE_TLS"] = "true"
            urllib3.disable_warnings(InsecureRequestWarning)

        # Optional: Set a tracking URI and an experiment
        exp_name = env_config.getenv("MLFLOW_EXPERIMENT_NAME", str)
        mlflow.set_tracking_uri(tracking_uri)
        mlflow.set_experiment(exp_name)
        return True
    except ImportError:
        return False
