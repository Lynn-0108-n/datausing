"""识别层统一出口：外部只需要 ``from app.identify import ...``。"""

from .masking import (
    MaskStrategy,
    mask,
    mask_by_strategy,
    mask_hits,
    mask_text_by_strategy,
    mask_value,
)
from .operators import check_l_diversity, generalize_numeric, laplace_noise, perturb_numeric
from .pipeline import DetectionPipeline, DetectOptions, DocumentReport, SegmentFinding
from .rules import RULES, detect
from .scoring import analyze, level_for, score
from .types import (
    ENTITY_LABELS,
    EntityType,
    Hit,
    Level,
    Result,
    entity_label,
    parse_entity_type,
)

__all__ = [
    "ENTITY_LABELS",
    "EntityType",
    "Hit",
    "Level",
    "MaskStrategy",
    "Result",
    "RULES",
    "DetectionPipeline",
    "DetectOptions",
    "DocumentReport",
    "SegmentFinding",
    "analyze",
    "check_l_diversity",
    "detect",
    "entity_label",
    "generalize_numeric",
    "laplace_noise",
    "level_for",
    "mask",
    "mask_by_strategy",
    "mask_hits",
    "mask_text_by_strategy",
    "mask_value",
    "parse_entity_type",
    "perturb_numeric",
    "score",
]
