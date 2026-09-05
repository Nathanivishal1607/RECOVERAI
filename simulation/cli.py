"""Synthetic-data generation CLI.

    python -m simulation.cli generate                       # dev size (~1,000 cases), seed 42
    python -m simulation.cli generate --size training       # ~10,000 cases
    python -m simulation.cli generate --size 200 --seed 7   # arbitrary size + seed
    python -m simulation.cli generate --scenario multi_cycle
    python -m simulation.cli generate --database-url sqlite:///sim.db --reset

By default it writes into ``settings.database_url``. ``--reset`` creates
the schema first (``Base.metadata.create_all``) — handy for a throwaway
SQLite file. Hidden ground truth is written to
``simulation/ground_truth/runs/<run_id>.json`` (never into the app DB).
"""

from __future__ import annotations

import argparse
import json
import sys

from simulation.config import DATASET_SIZES, SimConfig
from simulation.scenarios.library import SCENARIOS, get_scenario


def _resolve_size(value: str) -> int | str:
    if value in DATASET_SIZES:
        return value
    return int(value)


def _build_cfg(args) -> SimConfig:
    cfg = get_scenario(args.scenario, seed=args.seed)
    cfg = cfg.with_size(_resolve_size(args.size))
    if args.no_predictions:
        from dataclasses import replace

        cfg = replace(cfg, with_predictions=False)
    return cfg


def cmd_generate(args) -> int:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from backend.core.config import settings
    from backend.models import Base
    from simulation.generator.runner import run_simulation

    url = args.database_url or settings.database_url
    engine = create_engine(url, future=True)
    if url.startswith("sqlite"):
        from sqlalchemy import event

        @event.listens_for(engine, "connect")
        def _fast_sqlite(dbapi_conn, _rec):  # throwaway sim DBs — speed over durability
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA synchronous=OFF")
            cur.execute("PRAGMA journal_mode=MEMORY")
            cur.close()

    if args.reset:
        Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    cfg = _build_cfg(args)

    print(f"[sim] scenario={args.scenario} size={cfg.n_cases} seed={cfg.seed} "
          f"predictions={cfg.with_predictions}")
    print(f"[sim] db={url}")
    db = Session()
    try:
        res = run_simulation(db, cfg)
    finally:
        db.close()
        engine.dispose()

    out = res.as_dict()
    print(json.dumps(out, indent=2))
    print(f"[sim] done in {res.seconds}s  "
          f"cases={res.cases_created} recovered={res.recovered} "
          f"training_examples={res.training_examples}")
    print(f"[sim] oracle best-action mix: {res.oracle_best_action_counts}")
    print(f"[sim] ground truth: {res.ground_truth_path}")
    return 0


def cmd_scenarios(_args) -> int:
    for name in sorted(SCENARIOS):
        print(name)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="simulation.cli")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="generate a synthetic dataset")
    g.add_argument("--scenario", default="default", choices=sorted(SCENARIOS))
    g.add_argument("--size", default="development",
                   help="small | development | training | <int>")
    g.add_argument("--seed", type=int, default=42)
    g.add_argument("--database-url", default=None)
    g.add_argument("--reset", action="store_true",
                   help="create tables first (Base.metadata.create_all)")
    g.add_argument("--no-predictions", action="store_true",
                   help="skip placeholder Predictions + TrainingExample derivation")
    g.set_defaults(func=cmd_generate)

    sub.add_parser("scenarios", help="list scenario presets").set_defaults(
        func=cmd_scenarios
    )

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
