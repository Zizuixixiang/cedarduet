from abc import ABC, abstractmethod
from typing import Any


class GamePlugin(ABC):
    """A board-game plugin consumed by the common room framework."""

    game_type: str
    display_name: str
    rules_text: str
    move_format: str

    @abstractmethod
    def initial_state(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def validate_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def apply_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def check_winner(self, state: dict[str, Any]) -> str | None:
        """Return X, O, draw, or None."""
        raise NotImplementedError


def move_coordinates(move: dict[str, Any], size: int) -> tuple[int, int]:
    row = move.get("row")
    col = move.get("col")
    if (
        isinstance(row, bool)
        or isinstance(col, bool)
        or not isinstance(row, int)
        or not isinstance(col, int)
    ):
        raise ValueError("row 和 col 必须是整数")
    if not (0 <= row < size and 0 <= col < size):
        raise ValueError(f"坐标越界：row 和 col 必须在 0 到 {size - 1} 之间")
    return row, col

