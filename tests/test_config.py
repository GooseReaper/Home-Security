from pathlib import Path

from homecontrol.config import load_config

EXAMPLE = Path(__file__).parent.parent / "config" / "home.example.yaml"


def test_example_config_loads():
    config = load_config(EXAMPLE)
    assert config.rooms
    assert {"home", "night", "away"} <= set(config.modes)
    assert "camera.front_door" in config.entities_of("cameras")


def test_token_from_environment(monkeypatch):
    monkeypatch.setenv("HC_HA_TOKEN", "from-env")
    assert load_config(EXAMPLE).home_assistant.token == "from-env"


def test_example_enables_adblock_module_section():
    from homecontrol.modules import load_modules

    config = load_config(EXAMPLE)
    assert "adblock" in load_modules(config)
