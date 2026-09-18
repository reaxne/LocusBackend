"""Shared, conservative vocabulary for deterministic recommendation matching."""

import re
import unicodedata


# Each canonical concept intentionally groups only close equivalents. These
# terms are used by both interest and academic-strength matching so the two
# paths cannot silently drift apart.
CONCEPT_ALIASES: dict[str, frozenset[str]] = {
    "computing": frozenset({
        "computer science", "software engineering", "software development",
        "programming", "coding", "informatics", "информатика", "программирование",
        "бағдарламалау",
    }),
    "cybersecurity": frozenset({"cybersecurity", "cyber security", "information security"}),
    "artificial intelligence": frozenset({"artificial intelligence", "machine learning", "ai", "ии"}),
    "data science": frozenset({"data science", "data analytics", "data analysis", "big data"}),
    "engineering": frozenset({"engineering", "инженерия"}),
    "mathematics": frozenset({"mathematics", "math", "maths", "математика"}),
    "physics": frozenset({"physics", "физика"}),
    "research": frozenset({"research", "scientific research", "analytical", "laboratory", "lab"}),
    "writing": frozenset({"writing", "creative writing", "literature", "written communication"}),
    "collaboration": frozenset({"teamwork", "team work", "team project", "team projects", "collaboration", "communication"}),
}

LANGUAGE_ALIASES: dict[str, frozenset[str]] = {
    "english": frozenset({"en", "english", "английский"}),
    "russian": frozenset({"ru", "russian", "русский"}),
    "kazakh": frozenset({"kk", "kz", "kazakh", "қазақ", "казахский"}),
}

LEVEL_ALIASES: dict[str, frozenset[str]] = {
    "bachelor": frozenset({"bachelor", "bachelors", "undergraduate", "бакалавр"}),
    "master": frozenset({"master", "masters", "graduate", "магистр"}),
    "doctorate": frozenset({"doctorate", "phd", "doctoral", "докторантура"}),
}

_SIMPLE_ALIASES = {"астана": "astana", "алматы": "almaty"}


def clean_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _canonical(value: str, aliases: dict[str, frozenset[str]]) -> str | None:
    clean = clean_text(value)
    return next((canonical for canonical, values in aliases.items() if clean == canonical or clean in values), None)


def normalize(value: str) -> str:
    """Normalize an exact comparable value without inventing a broad match."""
    clean = clean_text(value)
    return _SIMPLE_ALIASES.get(clean, _canonical(clean, CONCEPT_ALIASES) or clean)


def concepts(value: str) -> set[str]:
    """Return explicit domain concepts contained in text, or exact tokens otherwise."""
    clean = clean_text(value)
    found = {
        canonical
        for canonical, values in CONCEPT_ALIASES.items()
        if any(re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", clean) for alias in {*values, canonical})
    }
    if found:
        return found
    return set(tokens(clean))


def language(value: str) -> str:
    return _canonical(value, LANGUAGE_ALIASES) or clean_text(value)


def level(value: str) -> str:
    return _canonical(value, LEVEL_ALIASES) or clean_text(value)


def tokens(value: str) -> list[str]:
    words = re.findall(r"\w+", clean_text(value))
    return [_SIMPLE_ALIASES.get(word, word) for word in words]
