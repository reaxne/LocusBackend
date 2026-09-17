"""Validate and atomically upsert a curator-supplied catalog; no web scraping."""

import argparse
from pathlib import Path

from pydantic import TypeAdapter

from database import Database
from recommendation.models import Program
from recommendation.repository import SQLiteProgramRepository


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog", type=Path, help="JSON array of program records")
    parser.add_argument("--database", help="Defaults to DATABASE_PATH or data/auth.db")
    args = parser.parse_args()
    programs = TypeAdapter(list[Program]).validate_json(args.catalog.read_text(encoding="utf-8"))
    keys = [(item.id, item.admission_year) for item in programs]
    if len(keys) != len(set(keys)):
        parser.error("Duplicate program id/admission year pairs in catalog")
    database = Database(args.database)
    database.initialize()
    SQLiteProgramRepository(database).save_programs(programs)
    demos = sum(item.is_demo for item in programs)
    print(f"Imported {len(programs)} program records; {demos} demo records are excluded from the production API.")


if __name__ == "__main__":
    main()
