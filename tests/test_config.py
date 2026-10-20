import pytest
import yaml

from sentiment import config


def test_example_config_loads(cfg):
    assert cfg.primary_product in cfg.products
    assert "Copilot" in cfg.products["GitHub Copilot"]
    assert set(cfg.aspects) == {"usability", "pricing", "accuracy", "reliability", "support"}
    assert cfg.themes["hallucinated_apis"]["label"] == "Hallucinated APIs"
    assert cfg.about_min == 0.6 and cfg.thresholds["min_n"] == 3
    assert cfg.relevance_re.search("Tried the new Copilot agent mode")
    assert not cfg.relevance_re.search("Best way to centre a div?")


def test_env_overrides_backend_and_model(monkeypatch, tmp_path):
    monkeypatch.setenv("JUDGE_BACKEND", "claude")
    monkeypatch.setenv("CLAUDE_MODEL", "claude-sonnet-5-5")
    c = config.load()
    assert c.judge["backend"] == "claude" and c.judge["claude_model"] == "claude-sonnet-5-5"


def _write(tmp_path, cfg_dict):
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(cfg_dict))
    return p


def _base(cfg_path):
    return yaml.safe_load(open(cfg_path))


@pytest.mark.parametrize("mutate, msg", [
    (lambda d: d.update(primary_product="Nope"), "primary_product"),
    (lambda d: d["thresholds"].pop("conf_min"), "thresholds missing"),
    (lambda d: d["thresholds"].update(about_min=1.5), "between 0 and 1"),
    (lambda d: d["themes"].update({"Bad Key": "x"}), "lower_snake_case"),
])
def test_invalid_config_is_rejected(tmp_path, mutate, msg):
    d = _base(config.DEFAULT_CONFIG)
    mutate(d)
    with pytest.raises(ValueError, match=msg):
        config.load(_write(tmp_path, d))


def test_data_dir_and_db_path(monkeypatch, tmp_path):
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert config.db_path() == tmp_path / "sentiment.db"
