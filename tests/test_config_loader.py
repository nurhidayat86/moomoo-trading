from pathlib import Path

import pytest
import yaml

from config_loader import load_config

EXAMPLE = Path(__file__).resolve().parent.parent / "src" / "config.yaml.example"


def test_load_example_config():
    config = load_config(EXAMPLE)
    assert config.symbols == ("US.AAPL",)
    assert config.collector.session == "ALL"
    assert config.required_subscription_slots == 2


def test_rejects_overnight_session(tmp_path: Path):
    path = tmp_path / "config.yaml"
    data = yaml.safe_load(EXAMPLE.read_text())
    data["collector"]["session"] = "OVERNIGHT"
    path.write_text(yaml.dump(data))
    with pytest.raises(ValueError, match="OVERNIGHT"):
        load_config(path)


def test_dedupes_symbols(tmp_path: Path):
    path = tmp_path / "config.yaml"
    data = yaml.safe_load(EXAMPLE.read_text())
    data["symbols"] = ["US.AAPL", "us.aapl", "US.MSFT"]
    path.write_text(yaml.dump(data))
    config = load_config(path)
    assert config.symbols == ("US.AAPL", "US.MSFT")


def test_invalid_symbol(tmp_path: Path):
    path = tmp_path / "config.yaml"
    data = yaml.safe_load(EXAMPLE.read_text())
    data["symbols"] = ["INVALID"]
    path.write_text(yaml.dump(data))
    with pytest.raises(ValueError, match="Invalid symbol"):
        load_config(path)
