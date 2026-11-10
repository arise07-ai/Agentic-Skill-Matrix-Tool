"""GAP RISK PREDICTION.

With OPENAI_API_KEY set, the LLM judges the risk of each required skill (prompts.GAP_RISK_SYSTEM / GAP_RISK_USER). Without a key,
or if the model call fails or returns something unusable, the transparent formula below is used instead, so the Gaps page never
breaks because of the model. Return a float in [0, 1] (1 = highest risk).
"""
import logging

from . import jev, llm, prompts
from ..config import get_settings

log = logging.getLogger(__name__)
_RISK_SCORES = {"LOW": 0.2, "MEDIUM": 0.5, "HIGH": 0.8}


def _heuristic(level: int, best_rating: int, coverage_ratio: float, near_count: int, upskill_count: int) -> float:
    shortfall_depth = max(0, level - best_rating) / 4
    no_bridge = float(coverage_ratio < 1 and near_count == 0 and upskill_count == 0)
    risk = 0.6 * (1 - coverage_ratio) + 0.25 * shortfall_depth + 0.15 * no_bridge
    return round(min(max(risk, 0.0), 1.0), 3)


def predict_risk(
    *, level: int, best_rating: int, coverage_ratio: float,
    near_count: int, upskill_count: int,
) -> float:
    settings = get_settings()
    if settings.typesafe_api_key:
        try:
            band, _ = jev.decide(
                state={
                    "required_level": level,
                    "best_rating": best_rating,
                    "coverage_ratio": round(coverage_ratio, 3),
                    "people_one_level_short": near_count,
                    "people_who_could_be_upskilled": upskill_count,
                },
                question="risk",
                instructions=(
                    "Classify the staffing risk that this required skill cannot be staffed at its required level. "
                    "Use the supplied values only. Fully covered skills are LOW risk; lower coverage and deeper skill "
                    "shortfalls increase risk; people close to the target or able to upskill reduce risk. "
                    "Choose exactly one risk band."
                ),
                options=tuple(_RISK_SCORES),
            )
            return _RISK_SCORES[band]
        except Exception as e:  # noqa: BLE001  continue through the configured fallback chain
            log.warning("Jev risk decision failed, trying the LLM: %s", type(e).__name__)

    if not llm.is_configured():
        return _heuristic(level, best_rating, coverage_ratio, near_count, upskill_count)
    try:
        result = llm.complete_json(
            prompts.gap_risk_system(),
            prompts.GAP_RISK_USER.format(
                level=level,
                best_rating=best_rating,
                coverage_ratio=round(coverage_ratio, 3),
                near_count=near_count,
                upskill_count=upskill_count,
            ),
            namespace="predict_risk",
        )
        risk = result.get("risk")
        if isinstance(risk, bool) or not isinstance(risk, (int, float)) or not 0 <= risk <= 1:
            raise ValueError("The model returned an invalid risk score")
        return round(float(risk), 3)
    except Exception as e:  # noqa: BLE001  fall back to the formula rather than failing the whole page
        log.warning("LLM risk prediction failed, using the formula: %s", e)
        return _heuristic(level, best_rating, coverage_ratio, near_count, upskill_count)
