import copy
import shutil

import yaml
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"
_EXAMPLE_PATH = Path(__file__).parent.parent / "config" / "settings.example.yaml"
_config: dict | None = None


def load_config() -> dict:
    global _config
    if _config is not None:
        return _config

    path = _CONFIG_PATH if _CONFIG_PATH.exists() else _EXAMPLE_PATH
    if not path.exists():
        raise FileNotFoundError(
            "No config found. Copy config/settings.example.yaml to config/settings.yaml and fill in your keys."
        )
    with open(path, "r", encoding="utf-8") as f:
        _config = yaml.safe_load(f) or {}
    return _config


def reload_config() -> dict:
    """Force-reload settings.yaml from disk (clears cached config)."""
    global _config
    _config = None
    return load_config()


def save_config(config: dict):
    """Write config dict to settings.yaml, backing up the previous version first."""
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _CONFIG_PATH.exists():
        shutil.copy2(_CONFIG_PATH, _CONFIG_PATH.with_suffix(".yaml.bak"))
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    global _config
    _config = None


def get(key: str, default=None):
    cfg = load_config()
    keys = key.split(".")
    val = cfg
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k)
        else:
            return default
        if val is None:
            return default
    return val


def set_key(key: str, value):
    """Set a dotted-path key in the config and save to disk."""
    cfg = copy.deepcopy(load_config())
    keys = key.split(".")
    node = cfg
    for k in keys[:-1]:
        if k not in node or not isinstance(node[k], dict):
            node[k] = {}
        node = node[k]
    node[keys[-1]] = value
    save_config(cfg)


def config_exists() -> bool:
    return _CONFIG_PATH.exists()
