"""UDEMY adapter interface. The learning-path service only talks to `UdemyClient`.

UDEMY_MODE=mock       -> MockUdemyClient (sample data, clearly flagged is_sample=True)
UDEMY_MODE=affiliate  -> AffiliateUdemyClient (needs UDEMY_CLIENT_ID / UDEMY_CLIENT_SECRET)
CUSTOMIZE: add a Udemy Business client by implementing `search` and registering it in get_client().
"""
from functools import lru_cache
from typing import Protocol

from pydantic import BaseModel

from ...config import get_settings


class Course(BaseModel):
    id: str
    title: str
    url: str
    level: str  # beginner | intermediate | expert | all
    skill: str
    duration_hours: float | None = None
    rating: float | None = None
    is_sample: bool = False


class UdemyClient(Protocol):
    def search(self, skill: str, level: str | None = None, limit: int = 3) -> list[Course]: ...


@lru_cache
def get_client() -> UdemyClient:
    s = get_settings()
    if s.udemy_mode == "affiliate":
        from .affiliate import AffiliateUdemyClient

        return AffiliateUdemyClient(s.udemy_client_id or "", s.udemy_client_secret or "")
    from .mock import MockUdemyClient

    return MockUdemyClient()
