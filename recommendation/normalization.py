import re
import unicodedata


ALIASES = {
    "math": "mathematics", "maths": "mathematics", "математика": "mathematics",
    "физика": "physics", "информатика": "computer science", "cs": "computer science",
    "ai": "artificial intelligence", "ии": "artificial intelligence",
    "робототехника": "robotics", "робототехникасы": "robotics",
    "программирование": "programming", "бағдарламалау": "programming",
    "астана": "astana", "алматы": "almaty",
}


def normalize(value: str) -> str:
    value = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    return ALIASES.get(value, value)


def tokens(value: str) -> list[str]:
    words = re.findall(r"\w+", unicodedata.normalize("NFKC", value).casefold())
    return [part for word in words for part in normalize(word).split()]
