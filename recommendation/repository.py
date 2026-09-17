from typing import Protocol

from database import Database
from recommendation.models import Program


class ProgramRepository(Protocol):
    def get_programs_for_entry_year(self, year: int) -> list[Program]: ...
    def get_program_by_id(self, program_id: str, year: int) -> Program | None: ...


class PostgreSQLProgramRepository:
    """One catalog query per recommendation request; production never returns demos."""

    def __init__(self, database: Database):
        self.database = database

    def get_programs_for_entry_year(self, year: int) -> list[Program]:
        return [Program.model_validate(item) for item in self.database.get_program_records(year)]

    def get_program_by_id(self, program_id: str, year: int) -> Program | None:
        item = self.database.get_program_record(program_id, year)
        return Program.model_validate(item) if item is not None else None

    def save_programs(self, programs: list[Program]) -> None:
        self.database.save_program_records([item.model_dump(mode="json") for item in programs])


# Compatibility alias for existing imports; storage is always PostgreSQL.
SQLiteProgramRepository = PostgreSQLProgramRepository
