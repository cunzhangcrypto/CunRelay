"""Configuration loader for CunRelay.

Loads config.yaml, resolves ${ENV_VAR} references, and provides
typed access to all settings.  The ``follow`` section can be
overridden via the ``FOLLOW_CONFIG`` environment variable (JSON
format) — useful for keeping the subscription list private.
"""

import json
import os
import re
from pathlib import Path

import yaml

_ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")


def _resolve_env(value: str) -> str:
    """Replace ${VAR} references with environment variable values."""
    def _replace(m: re.Match) -> str:
        var_name = m.group(1)
        val = os.environ.get(var_name)
        if val is None:
            print(f"  [Config] Warning: environment variable '{var_name}' is not set. Leaving empty.")
            return ""
        return val
    return _ENV_VAR_PATTERN.sub(_replace, value)


def _resolve_dict(d: dict) -> dict:
    """Recursively resolve environment variables in a dict."""
    for k, v in d.items():
        if isinstance(v, str):
            d[k] = _resolve_env(v)
        elif isinstance(v, dict):
            d[k] = _resolve_dict(v)
        elif isinstance(v, list):
            d[k] = [_resolve_env(item) if isinstance(item, str) else item for item in v]
    return d


def _load_dotenv(root: Path) -> None:
    """Load .env file from project root into environment variables."""
    dotenv_path = root / ".env"
    if not dotenv_path.exists():
        return
    with open(dotenv_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            if key and key not in os.environ:
                os.environ[key] = val


def project_root() -> Path:
    """Project root — the directory that contains config/ and pyproject.toml."""
    return Path(__file__).resolve().parent.parent


def load_config(path: str | None = None) -> dict:
    """Load and resolve the configuration file."""
    root = project_root()
    _load_dotenv(root)

    if path is None:
        path = str(root / "config" / "config.yaml")

    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    config = _resolve_dict(raw)

    # Override follow list from environment variable (JSON format)
    follow_json = os.environ.get("FOLLOW_CONFIG")
    if follow_json:
        try:
            config["follow"] = json.loads(follow_json)
            print("  [Config] Follow list loaded from FOLLOW_CONFIG env var")
        except json.JSONDecodeError as e:
            print(f"  [Config] Warning: FOLLOW_CONFIG is not valid JSON: {e}")

    return config
