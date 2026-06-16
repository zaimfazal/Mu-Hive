import os

def load_config(config_name: str) -> dict:
    """Loads a YAML configuration file from src/config/."""
    try:
        import yaml

        config_path = os.path.join("src", "config", f"{config_name}.yaml")
        if not os.path.exists(config_path):
            return {}
        with open(config_path, "r") as f:
            return yaml.safe_load(f)
    except Exception:
        return {}
