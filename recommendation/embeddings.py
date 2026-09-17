"""Replaceable text encoders with a bounded, content-keyed program cache."""

import hashlib
import math
from collections import OrderedDict
from threading import RLock
from typing import Protocol, Sequence

from recommendation.models import Program, StudentProfile
from recommendation.normalization import tokens


class EmbeddingProvider(Protocol):
    name: str

    def encode(self, texts: Sequence[str]) -> list[list[float]]: ...


class KeywordEmbeddingProvider:
    """Offline bag-of-words fallback; lexical matching, not a neural semantic model."""

    name = "deterministic_keyword_v1"

    def __init__(self, dimensions: int = 2048):
        self.dimensions = dimensions

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vector = [0.0] * self.dimensions
            for token in set(tokens(text)):
                index = int.from_bytes(hashlib.sha256(token.encode()).digest()[:8], "big") % self.dimensions
                vector[index] = 1.0
            vectors.append(vector)
        return vectors


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("Embedding dimensions differ")
    norm = math.sqrt(sum(value * value for value in left) * sum(value * value for value in right))
    if not norm:
        return 0.0
    return max(0.0, min(1.0, sum(a * b for a, b in zip(left, right)) / norm))


def program_text(program: Program) -> str:
    return ". ".join(filter(None, [
        program.name, program.description, *program.interests, *program.tags,
        *program.important_courses, *program.career_paths,
    ]))


class InterestMatcher:
    def __init__(self, provider: EmbeddingProvider | None = None, cache_size: int = 4096):
        self.provider = provider or KeywordEmbeddingProvider()
        self.cache_size = cache_size
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = RLock()

    def _encode(self, texts: list[str]) -> list[list[float]]:
        vectors = self.provider.encode(texts)
        if len(vectors) != len(texts) or any(
            not vector or any(not math.isfinite(value) for value in vector) for vector in vectors
        ):
            raise ValueError("Embedding provider returned invalid vectors")
        return vectors

    def scores(self, student: StudentProfile, programs: list[Program]) -> dict[str, float | None]:
        text = ". ".join([*student.interest, *student.extracurricular_interests])
        if not text or not programs:
            return {item.id: None for item in programs}
        texts = [program_text(item) for item in programs]
        # Serialize access to providers that are not thread-safe; batch only cache misses.
        with self._lock:
            missing = list(dict.fromkeys(item for item in texts if item not in self._cache))
            fresh = dict(zip(missing, self._encode(missing))) if missing else {}
            vectors = [fresh[item] if item in fresh else self._cache[item] for item in texts]
            for item, vector in zip(texts, vectors):
                self._cache[item] = vector
                self._cache.move_to_end(item)
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)
            student_vector = self._encode([text])[0]
        return {item.id: cosine_similarity(student_vector, vector)
                for item, vector in zip(programs, vectors)}
