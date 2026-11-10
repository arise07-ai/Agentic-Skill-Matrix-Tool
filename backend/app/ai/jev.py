"""Shared access to Jev's fixed-choice decision API."""
import math
from collections.abc import Mapping, Sequence

from . import AINotConfigured
from ..config import get_settings


def decide(
    *,
    state: Mapping[str, object],
    question: str,
    instructions: str,
    options: Sequence[str],
) -> tuple[str, float]:
    """Return a validated option and its confidence from Jev."""
    if not get_settings().typesafe_api_key:
        raise AINotConfigured("TYPESAFE_API_KEY is not set")
    if not question or len(options) < 2 or len(set(options)) != len(options):
        raise ValueError("A Jev decision needs a question and at least two unique options")

    from typesafe_sdk import Choice, TypeSafeClient

    with TypeSafeClient() as client:
        result = client.system_one(
            state=dict(state),
            questions={
                question: Choice(
                    instructions=instructions,
                    criteria={option: None for option in options},
                )
            },
        )

    answer = result.choices[question]
    choice = answer.choice
    if choice not in options:
        raise ValueError("Jev returned a choice outside the allowed options")
    confidence = float(answer.confidence)
    if not math.isfinite(confidence):
        raise ValueError("Jev returned a non-finite confidence")
    return choice, min(1.0, max(0.0, confidence))
