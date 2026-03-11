from dataclasses import dataclass
from pathlib import Path
from typing import Any


def parse_scalar(value: str) -> Any:
    text = value.strip()
    if text == "":
        return None

    if text.startswith(('"', "'")) and text.endswith(('"', "'")):
        return text[1:-1]

    lowered = text.lower()
    if lowered in {"null", "none", "~"}:
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False

    if text.lstrip("-").isdigit():
        return int(text)

    return text


def load_yaml_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}

    config: dict[str, Any] = {}
    current_section: str | None = None

    for raw_line in config_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue

        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        if indent == 0 and stripped.endswith(":"):
            current_section = stripped[:-1].strip()
            config[current_section] = {}
            continue

        if ":" not in stripped:
            raise ValueError(f"Invalid YAML line in {config_path.name}: {raw_line}")

        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        value = parse_scalar(raw_value)

        if indent == 0:
            config[key] = value
            current_section = None
            continue

        if current_section is None:
            raise ValueError(f"Nested YAML key without section in {config_path.name}: {raw_line}")

        section = config.setdefault(current_section, {})
        if not isinstance(section, dict):
            raise ValueError(f"Section {current_section} must be a mapping in {config_path.name}")
        section[key] = value

    return config


def get_section_value(config: dict[str, Any], section: str, key: str, default: Any = None) -> Any:
    section_data = config.get(section, {})
    if not isinstance(section_data, dict):
        return default
    return section_data.get(key, default)


YAML_DIR = Path(__file__).with_name("yaml")
CONFIG_PATH = YAML_DIR / "config.yaml"
CONFIG = load_yaml_config(CONFIG_PATH)


@dataclass(frozen=True)
class PokemonSettings:
    showdown_username: str | None = get_section_value(CONFIG, "showdown", "username")
    showdown_password: str | None = get_section_value(CONFIG, "showdown", "password")
    battle_format: str = get_section_value(CONFIG, "battle", "format", "gen9randombattle")
    matchmaking_mode: str = get_section_value(CONFIG, "matchmaking", "mode", "ladder")
    challenge_target_username: str | None = get_section_value(CONFIG, "matchmaking", "challenge_target_username")
    matches_per_activation: int = int(get_section_value(CONFIG, "matchmaking", "matches_per_activation", 1))
    openai_api_key: str | None = get_section_value(CONFIG, "openai", "api_key")
    openai_model: str = get_section_value(CONFIG, "openai", "model", "gpt-5.2")
    openai_base_url: str = get_section_value(CONFIG, "openai", "base_url", "https://right.codes/codex/v1")


POKEMON_SETTINGS = PokemonSettings()


LAST_ACTION_PATH = Path(__file__).with_name("last_action.txt")


def write_last_action(message: str) -> None:
    try:
        LAST_ACTION_PATH.write_text(message.strip() or "No actions recorded yet.", encoding="utf-8")
    except Exception as exc:
        print(f"WARN: Failed to write last action to '{LAST_ACTION_PATH}': {exc}")


def ensure_last_action_file() -> None:
    if LAST_ACTION_PATH.exists():
        return

    write_last_action("No actions recorded yet.")
