"""Command line.

    python -m sentiment run                     # daily run: collect -> infer -> judge
    python -m sentiment run --backfill-days 30  # one-off: wider window, no thread cap
    python -m sentiment seed-demo [--days 90]   # synthetic data, fictional products
    python -m sentiment init-db                 # create an empty database
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime

from sentiment import collect, config, judge, store

BATCH = 300


def _log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def run(cfg, con, backfill_days: int | None = None) -> int:
    from sentiment.backends import Usage, get_backend

    backend = get_backend(cfg)
    _log(f"judge backend: {backend.name} ({backend.model})")

    if backfill_days:
        stats = asyncio.run(collect.collect(con, cfg, _log, window_days=backfill_days,
                                            max_threads=100_000))
    else:
        stats = asyncio.run(collect.collect(con, cfg, _log))
    _log(f"collected: {stats}")

    # Batch-drain loops. Each stops on ANY failure: failed rows stay queued for the next
    # run, so carrying on would re-ask the same rows forever.
    checked = inferred = tfail = 0
    tusage = Usage()
    while True:
        c, n, f, u = judge.infer_targets(con, cfg, backend, limit=BATCH)
        checked, inferred, tfail = checked + c, inferred + n, tfail + f
        tusage += u
        if c < BATCH or f:
            break
    if checked:
        _log(f"target check: {checked} unnamed comment(s), {inferred} inferred, {tfail} failed "
             f"({tusage.input_tokens} in / {tusage.output_tokens} out tokens)")

    total = failed = 0
    jusage = Usage()
    while True:
        j, f, u = judge.judge_pending(con, cfg, backend, limit=BATCH)
        total, failed = total + j, failed + f
        jusage += u
        if j == 0 or f:
            break
    _log(f"judged {total} mention(s), {failed} failed (retried next run) "
         f"({jusage.input_tokens} in / {jusage.output_tokens} out tokens; "
         f"{backend.stats['rate_limited']} rate-limited)")
    # Fail the run only if nothing could be judged at all (e.g. bad key, outage).
    return 1 if failed and not total else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="sentiment")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="collect, infer and judge")
    r.add_argument("--backfill-days", type=int)
    d = sub.add_parser("seed-demo", help="fill the database with synthetic demo data")
    d.add_argument("--days", type=int, default=90)
    d.add_argument("--seed", type=int, default=7)
    sub.add_parser("init-db", help="create an empty database")
    args = ap.parse_args(argv)

    con = store.connect()
    if args.cmd == "run":
        if store.is_demo(con):
            _log(f"{config.db_path()} holds synthetic demo data; delete it or set DATA_DIR "
                 "to a fresh directory before a real run")
            return 2
        return run(config.load(), con, args.backfill_days)
    if args.cmd == "seed-demo":
        from sentiment import demo
        if con.execute("SELECT 1 FROM threads WHERE reddit_id NOT LIKE 't3_demo%' LIMIT 1"
                       ).fetchone():
            _log(f"{config.db_path()} holds real data; use a fresh DATA_DIR for the demo")
            return 2
        cfg = config.load(os.environ.get("DEMO_CONFIG_PATH") or config.DEMO_CONFIG)
        n = demo.seed(con, cfg, days=args.days, seed=args.seed)
        _log(f"demo: wrote {n['threads']} threads, {n['items']} items, "
             f"{n['mentions']} judged mentions to {config.db_path()}")
        return 0
    _log(f"database ready at {config.db_path()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
