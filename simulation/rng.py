"""Deterministic RNG helper.

One master ``seed`` fans out into independent, reproducible per-stream
generators keyed by a label + integer, so generating merchant 7's
attributes never depends on how many customers were drawn first.
"""

from __future__ import annotations

import hashlib
import random


def _derive(seed: int, *key: object) -> int:
    h = hashlib.sha256(("|".join([str(seed), *map(str, key)])).encode()).digest()
    return int.from_bytes(h[:8], "big")


def stream(seed: int, *key: object) -> random.Random:
    """A fresh :class:`random.Random` deterministically derived from
    ``(seed, *key)``."""
    return random.Random(_derive(seed, *key))
