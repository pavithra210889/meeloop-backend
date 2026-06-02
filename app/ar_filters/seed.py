"""
Seed script for AR filters and game configs.

Run from the backend/ directory:
    python -m app.ar_filters.seed

Idempotent: skips rows that already exist.
"""

import json
import os
import sys
from pathlib import Path

# Ensure the backend package is importable when run as __main__
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from sqlmodel import Session, select
from app.database import engine
from app.ar_filters.models import ArFilter, ArGameConfig

# Path to the bundled filters.json (relative to repo root)
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_FILTERS_JSON = _REPO_ROOT / "frontend" / "app" / "src" / "main" / "assets" / "ar_filters" / "filters.json"

GAME_DEFAULTS = {
    "mouth_catch": {
        "catchRadiusY": 0.14,
        "catchRadiusX": 0.22,
        "mouthThreshold": 0.04,
        "baseSpeed": 0.22,
        "speedPerPoint": 0.003,
        "baseInterval": 1.8,
        "minInterval": 0.65,
        "comboThreshold": 3,
        "emojis": ["🍎", "🍊", "🍋", "🍇", "🍓", "🍉", "🌮", "🍕", "⭐", "🍩", "🍬", "🌟", "🍦", "🥑"],
    },
    "eyebrow_flap": {
        "gravity": 2.0,
        "flapStrength": -0.85,
        "maxFallSpeed": 1.5,
        "pipeSpeedBase": 0.28,
        "pipeSpeedBonus": 0.005,
        "pipeWidth": 0.14,
        "gapHeight": 0.34,
        "spawnInterval": 2.0,
        "birdX": 0.22,
        "birdHitRadius": 0.025,
        "browThreshold": 0.08,
        "tiltDeadZone": 0.05,
        "tiltSensitivity": 0.4,
        "restartDelay": 2.0,
    },
}


def seed_filters(session: Session) -> int:
    """Insert filters from filters.json. Returns count of inserted rows."""
    if not _FILTERS_JSON.exists():
        print(f"WARNING: filters.json not found at {_FILTERS_JSON}")
        return 0

    with open(_FILTERS_JSON, "r", encoding="utf-8") as fh:
        filter_list = json.load(fh)

    inserted = 0
    for idx, filter_data in enumerate(filter_list):
        filter_key = filter_data.get("id")
        if not filter_key:
            print(f"  SKIP: entry at index {idx} has no 'id' field")
            continue

        existing = session.exec(
            select(ArFilter).where(ArFilter.filter_key == filter_key)
        ).first()

        if existing:
            print(f"  SKIP filter '{filter_key}' (already exists)")
            continue

        row = ArFilter(
            filter_key=filter_key,
            filter_data=filter_data,
            is_active=True,
            sort_order=idx,
        )
        session.add(row)
        inserted += 1
        print(f"  INSERT filter '{filter_key}'")

    session.commit()
    return inserted


def seed_game_configs(session: Session) -> int:
    """Insert game configs from GAME_DEFAULTS. Returns count of inserted rows."""
    inserted = 0
    for game_id, config_data in GAME_DEFAULTS.items():
        existing = session.exec(
            select(ArGameConfig).where(ArGameConfig.game_id == game_id)
        ).first()

        if existing:
            print(f"  SKIP game config '{game_id}' (already exists)")
            continue

        row = ArGameConfig(game_id=game_id, config_data=config_data)
        session.add(row)
        inserted += 1
        print(f"  INSERT game config '{game_id}'")

    session.commit()
    return inserted


def main():
    print("=== AR Filters Seed ===")
    with Session(engine) as session:
        print("\n-- Seeding filters --")
        n_filters = seed_filters(session)

        print("\n-- Seeding game configs --")
        n_games = seed_game_configs(session)

    print(f"\nDone. Inserted {n_filters} filter(s), {n_games} game config(s).")


if __name__ == "__main__":
    main()
