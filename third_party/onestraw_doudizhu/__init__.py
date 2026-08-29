"""Minimal vendored rule surface adapted from onestraw/doudizhu 0.1.5."""

from .core import (
    PATTERN_ENTRY_COUNT,
    RANK_INDEX,
    RANK_LABELS,
    CardPattern,
    RankPlay,
    can_beat,
    classify_ranks,
    legal_rank_plays,
    pattern_from_public,
)

__all__ = [
    "PATTERN_ENTRY_COUNT",
    "RANK_INDEX",
    "RANK_LABELS",
    "CardPattern",
    "RankPlay",
    "can_beat",
    "classify_ranks",
    "legal_rank_plays",
    "pattern_from_public",
]
