import os
import yaml

_config_cache = None


def load_config():
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    config_path = os.environ.get("CONFIG_PATH", "config.yaml")
    with open(config_path, "r") as f:
        _config_cache = yaml.safe_load(f)
    return _config_cache


def get_datasource_config():
    return load_config()["datasource"]


def get_feishu_config():
    return load_config()["feishu"]


def get_apisports_config():
    cfg = load_config().get("apisports", {})
    # 兼容旧格式 key / 新格式 keys
    keys = cfg.get("keys", [])
    if not keys and cfg.get("key"):
        keys = [cfg["key"]]
    return {"keys": keys}


def get_juhe_api_config():
    return load_config()["juhe_api"]
