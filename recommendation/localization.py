"""Russian presentation values for public recommendation responses.

Catalog identifiers and admission-route codes stay intact for the API contract,
but every value rendered by the client is localized at the response boundary.
"""
from __future__ import annotations

import re


_VALUES = {
    "Nazarbayev University": "Назарбаев Университет",
    "Satbayev University": "Казахский национальный исследовательский технический университет имени К. И. Сатпаева",
    "Astana IT University": "Астанинский университет информационных технологий",
    "Maqsut Narikbayev University": "Университет имени Максута Нарикбаева",
    "Almaty Management University": "Алматинский университет менеджмента",
    "Международная образовательная корпорация (KazGASA)": "Международная образовательная корпорация",
    "Bachelor of Science in Computer Science": "Бакалавр наук по компьютерным наукам",
    "Bachelor of Science in Biological Sciences": "Бакалавр наук по биологическим наукам",
    "Software Engineering": "Программная инженерия",
    "Big Data Analysis": "Анализ больших данных",
    "Cybersecurity": "Кибербезопасность",
    "Electronic Engineering": "Электронная инженерия",
    "Digital Journalism": "Цифровая журналистика",
    "Data Science": "Наука о данных",
    "Математика (IP)": "Математика",
    "English": "английский",
    "Mathematics": "математика",
    "Computer Science": "компьютерные науки",
    "Biology": "биология",
    "Chemistry": "химия",
    "Literature": "литература",
    "Communication": "коммуникация",
    "History": "история",
    "Drawing": "рисование",
    "Art": "искусство",
    "Geography": "география",
    "Physics": "физика",
    "Physical Education": "физическая культура",
    "Programming": "программирование",
    "Robotics": "робототехника",
    "Research": "исследовательская работа",
    "Writing": "письменная речь",
    "Teamwork": "командная работа",
    "Hackathons": "хакатоны",
    "Volunteering": "волонтёрство",
    "Personal Projects": "личные проекты",
    "Competitions": "конкурсы",
    "computer science": "компьютерные науки",
    "software development": "разработка программного обеспечения",
    "biology": "биология",
    "journalism": "журналистика",
    "sociology": "социология",
    "psychology": "психология",
    "political science": "политология",
    "graphic design": "графический дизайн",
    "geology": "геология",
    "hydrogeology": "гидрогеология",
    "robotics": "робототехника",
    "architecture": "архитектура",
    "data science": "наука о данных",
    "machine learning": "машинное обучение",
    "cybersecurity": "кибербезопасность",
    "electronics": "электроника",
    "petroleum engineering": "нефтегазовое дело",
    "finance": "финансы",
    "accounting": "учёт и аудит",
    "marketing": "маркетинг",
    "international relations": "международные отношения",
    "law": "право",
    "fintech": "финансовые технологии",
    "pharmacy": "фармация",
    "dentistry": "стоматология",
    "medicine": "медицина",
    "pediatrics": "педиатрия",
    "nursing": "сестринское дело",
    "agronomy": "агрономия",
    "bioengineering": "биоинженерия",
    "bioinformatics": "биоинформатика",
    "veterinary": "ветеринария",
    "logistics": "логистика",
    "mathematics": "математика",
    "physics": "физика",
    "translation": "переводческое дело",
    "languages": "языки",
    "hospitality": "гостеприимство",
    "management": "менеджмент",
    "interior design": "дизайн интерьера",
    "industrial design": "промышленный дизайн",
    "fashion design": "дизайн одежды",
    "acting": "актёрское искусство",
    "directing": "режиссура",
    "energy": "энергетика",
    "water resources": "водные ресурсы",
    "forestry": "лесное хозяйство",
    "education": "образование",
    "sport": "спорт",
    "choreography": "хореография",
    "textile": "текстильное производство",
    "food technology": "технология пищевых производств",
    "history": "история",
    "archeology": "археология",
    "active tourism": "активный туризм",
    "tourism": "туризм",
    "state_grant": "государственный грант",
    "university_scholarship": "университетская стипендия",
    "self_funded": "самостоятельная оплата",
}
_LATIN = re.compile(r"[A-Za-z]")


def text(value: str | None) -> str | None:
    """Return a client-safe Russian label without exposing untranslated catalog text."""
    if value is None:
        return None
    value = _VALUES.get(value, value)
    if _LATIN.search(value) and not re.search(r"[А-Яа-яЁё]", value):
        return "Сведения доступны в официальном источнике"
    return value


def values(items: list[str]) -> list[str]:
    return [text(item) or "" for item in items]


def localize_response(response):
    """Localize all presentation fields in a RecommendationResponse in place."""
    summary = response.student_summary
    summary.main_interests = values(summary.main_interests)
    summary.academic_strengths = values(summary.academic_strengths)
    summary.constraints = values(summary.constraints)
    summary.country = text(summary.country)
    summary.study_language = text(summary.study_language)
    for excluded in response.excluded_programs:
        excluded.reasons = values(excluded.reasons)
    for item in response.recommendations:
        item.university = text(item.university) or "Университет"
        item.program = text(item.program) or "Образовательная программа"
        item.city = text(item.city)
        item.interests = values(item.interests)
        item.languages = values(item.languages)
        item.program_group = text(item.program_group)
        item.why_recommended = values(item.why_recommended)
        item.warnings = values(item.warnings)
        item.financial.funding_options = values(item.financial.funding_options)
        for task in item.roadmap:
            task.title = text(task.title) or "Следующий шаг"
            task.description = text(task.description) or "Ознакомьтесь с официальной информацией."
            task.reason = text(task.reason) or "Это поможет подготовиться к поступлению."
            task.how = values(task.how)
            task.timing = text(task.timing) or "В ближайшую неделю"
            task.completion_criteria = text(task.completion_criteria) or "Действие выполнено."
        if item.next_action:
            item.next_action.title = text(item.next_action.title) or "Следующий шаг"
            item.next_action.description = text(item.next_action.description) or "Ознакомьтесь с официальной информацией."
            item.next_action.reason = text(item.next_action.reason) or "Это поможет подготовиться к поступлению."
            item.next_action.how = values(item.next_action.how)
    response.warnings = values(response.warnings)
    response.matching_method = "Сопоставление по интересам"
    return response
