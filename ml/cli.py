"""ML training CLI.

Phase 3 (S-learner baseline):
    python -m ml.cli train                         # S-learner, from settings.database_url
    python -m ml.cli train --version v1 --promote  # DRAFT -> VALIDATED -> PROMOTED

Phase 4 (incremental / uplift candidates):
    python -m ml.cli train --kind t_learner --promote     # promote the T-learner
    python -m ml.cli train --kind lgbm_s_learner --database-url sqlite:///sim.db

``train`` writes the artifact + registers a DRAFT ``ModelVersion``; with
``--promote`` it advances DRAFT -> VALIDATED -> PROMOTED iff the held-out
ROC-AUC clears ``--min-roc`` (retiring any current PROMOTED for the role
first — "one PROMOTED per model_role").

The Phase 4 model bake-off (against the simulator oracle — evaluation
only) is a separate entry point that must not be imported from ``ml/``:
    python -m simulation.evaluation.phase4_compare --n-cases 1500
"""

from __future__ import annotations

import argparse
import json
import sys

_S_KIND = "s_learner"


def cmd_train(args) -> int:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from backend.core.config import settings
    from backend.models import enums
    from backend.repositories.governance import ModelVersionRepository
    from ml.training.train import MODEL_ROLE, train_recovery_model
    from ml.training.uplift import train_uplift_model

    url = args.database_url or settings.database_url
    engine = create_engine(url, future=True)
    Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    db = Session()
    try:
        if args.kind == _S_KIND:
            result = train_recovery_model(db, version=args.version, seed=args.seed)
        else:
            result = train_uplift_model(
                db, kind=args.kind, version=args.version, seed=args.seed
            )
        evaluation = result.evaluation
        print(json.dumps(result.summary(), indent=2, default=str))

        if args.promote:
            roc = (evaluation.get("validation") or {}).get("roc_auc")
            if roc is None or roc < args.min_roc:
                print(
                    f"[ml] NOT promoting: validation ROC-AUC={roc} "
                    f"< --min-roc {args.min_roc}"
                )
                return 0
            repo = ModelVersionRepository(db)
            current = repo.promoted_for_role(MODEL_ROLE)
            if current is not None:
                repo.transition_status(current, enums.ModelVersionStatus.RETIRED.value)
                print(
                    f"[ml] retired previous PROMOTED {current.model_name} "
                    f"{current.version}"
                )
            mv = repo.get(result.model_version.id)
            repo.transition_status(mv, enums.ModelVersionStatus.VALIDATED.value)
            repo.transition_status(mv, enums.ModelVersionStatus.PROMOTED.value)
            db.commit()
            print(f"[ml] PROMOTED {mv.model_name} {mv.version} (ROC-AUC={roc:.3f})")
    finally:
        db.close()
        engine.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ml.cli")
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("train", help="train a model + register a ModelVersion")
    t.add_argument(
        "--kind",
        default=_S_KIND,
        choices=["s_learner", "t_learner", "tree_s_learner", "lgbm_s_learner"],
    )
    t.add_argument("--database-url", default=None)
    t.add_argument("--version", default=None,
                   help="ModelVersion identity (default: timestamp)")
    t.add_argument("--seed", type=int, default=42)
    t.add_argument("--promote", action="store_true",
                   help="DRAFT -> VALIDATED -> PROMOTED if it passes")
    t.add_argument("--min-roc", type=float, default=0.55,
                   help="min validation ROC-AUC to promote")
    t.set_defaults(func=cmd_train)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
