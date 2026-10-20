from sentiment import __main__ as cli
from sentiment import config, demo, store


def test_demo_config_is_fictional_overlay(cfg):
    d = config.load(config.DEMO_CONFIG)
    assert d.primary_product == "Codewren"
    assert not set(d.products) & set(cfg.products)          # no real vendors in the demo
    assert d.themes == cfg.themes and d.thresholds == cfg.thresholds   # inherited


def test_seed_demo(con):
    d = config.load(config.DEMO_CONFIG)
    n = demo.seed(con, d, days=30, seed=1)
    assert n["threads"] > 30 and n["mentions"] > 50
    assert store.is_demo(con)
    counted = con.execute("SELECT count(*) FROM mentions WHERE about >= ? AND sentiment_conf >= ?",
                          (d.about_min, d.conf_min)).fetchone()[0]
    assert 0 < counted < n["mentions"]          # the gates have something to drop
    assert con.execute("SELECT count(*) FROM threads WHERE title NOT LIKE '[demo]%'"
                       ).fetchone()[0] == 0
    products = {r[0] for r in con.execute("SELECT DISTINCT product FROM mentions")}
    assert products <= set(d.products)
    # idempotent: a second seed replaces rather than duplicates
    demo.seed(con, d, days=30, seed=1)
    assert con.execute("SELECT count(*) FROM threads").fetchone()[0] == n["threads"]


def test_cli_keeps_demo_and_real_data_apart(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DB_PATH", raising=False)
    assert cli.main(["seed-demo", "--days", "5"]) == 0
    assert cli.main(["run"]) == 2               # refuses to mix real data into a demo DB
