import json
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field, TypeAdapter

from database import Database
from recommendation.models import Program


class ProgramRepository(Protocol):
    def get_programs_for_entry_year(self, year: int) -> list[Program]: ...
    def get_program_by_id(self, program_id: str, year: int) -> Program | None: ...


class _UniversityCatalogEntry(BaseModel):
    id: str
    programs: list[Program]


class _UniversityCatalog(BaseModel):
    # Other fields contain catalog metadata, not Program attributes.
    universities: list[_UniversityCatalogEntry]
    programCycleOverrides: list[Program] = Field(default_factory=list)


class JSONProgramRepository:
    """Validated, read-only snapshot of either supported JSON catalog format.

    A cycle-specific record overrides the generic record even when it is
    inactive or a demo. Returned models are copies so callers cannot change
    the shared catalog. Recreate the repository to load file changes.
    """

    DEFAULT_PATH = Path(__file__).resolve().parents[1] / "catalogs" / "kazakhstan_programs.json"

    def __init__(self, path: str | Path = DEFAULT_PATH):
        self.path = Path(path)
        content = self.path.read_text(encoding="utf-8-sig")
        try:
            data = json.loads(content)
            if isinstance(data, list):
                programs = TypeAdapter(list[Program]).validate_python(data)
            else:
                catalog = _UniversityCatalog.model_validate(data)
                universities = {item.id for item in catalog.universities}
                if len(universities) != len(catalog.universities):
                    raise ValueError("Duplicate university id")
                programs = []
                for university in catalog.universities:
                    for program in university.programs:
                        if program.university_id != university.id:
                            raise ValueError(f"Program {program.id!r} belongs to a different university")
                        programs.append(program)
                for program in catalog.programCycleOverrides:
                    if program.university_id not in universities:
                        raise ValueError(f"Program {program.id!r} refers to an unknown university")
                programs.extend(catalog.programCycleOverrides)

            index: dict[str, dict[int | None, Program]] = {}
            for program in programs:
                versions = index.setdefault(program.id, {})
                if program.admission_year in versions:
                    raise ValueError(
                        f"Duplicate program id/admission year: {program.id!r}/{program.admission_year}"
                    )
                versions[program.admission_year] = program
        except ValueError as exc:
            raise ValueError(f"Invalid program catalog {self.path}: {exc}") from exc
        self._programs = index

    def get_programs_for_entry_year(self, year: int) -> list[Program]:
        return [
            program for program_id in sorted(self._programs)
            if (program := self.get_program_by_id(program_id, year)) is not None
        ]

    def get_program_by_id(self, program_id: str, year: int) -> Program | None:
        versions = self._programs.get(program_id, {})
        program = versions.get(year, versions.get(None))
        if program is None or not program.active or program.is_demo:
            return None
        return program.model_copy(deep=True)


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
