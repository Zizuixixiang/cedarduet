"""Vendored, integration-neutral three-card hand evaluator."""

from .evaluator import (
    HAND_TYPE_LABELS,
    HAND_TYPE_STRENGTH,
    HandValue,
    compare_hands,
    evaluate_hand,
)

__all__ = [
    "HAND_TYPE_LABELS",
    "HAND_TYPE_STRENGTH",
    "HandValue",
    "compare_hands",
    "evaluate_hand",
]
