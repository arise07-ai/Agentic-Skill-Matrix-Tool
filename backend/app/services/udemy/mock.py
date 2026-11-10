"""Sample Udemy data so the learning-path flow works without credentials.
Titles are generated placeholders (is_sample=True). URLs point to a real Udemy *search page*
for the skill, never to a made-up course."""
from urllib.parse import quote_plus

from .base import Course

_TEMPLATES = [
    ("beginner", "{s} Fundamentals (sample course)", 6.0, 4.5),
    ("intermediate", "{s} in Practice: Projects & Patterns (sample course)", 10.0, 4.6),
    ("expert", "Advanced {s}: Production Mastery (sample course)", 14.0, 4.7),
]


class MockUdemyClient:
    def search(self, skill: str, level: str | None = None, limit: int = 3) -> list[Course]:
        url = f"https://www.udemy.com/courses/search/?q={quote_plus(skill)}"
        courses = [
            Course(id=f"sample-{skill.lower().replace(' ', '-')}-{lv}", title=t.format(s=skill), url=url,
                   level=lv, skill=skill, duration_hours=h, rating=r, is_sample=True)
            for lv, t, h, r in _TEMPLATES
        ]
        courses.sort(key=lambda c: 0 if level and c.level == level else 1)
        return courses[:limit]
