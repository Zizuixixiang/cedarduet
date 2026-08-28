from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MoveResult:
    """Optional metadata returned by plugins after a legal move."""

    state: dict[str, Any]
    retain_turn: bool = False
    note: str = ""
    next_player_id: str | None = None
    inactive_player_ids: list[str] = field(default_factory=list)
    skipped_player_ids: list[str] = field(default_factory=list)
    participant_activity: dict[str, str] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    settlement_deltas: dict[str, int] | None = None
    event_visible_to_player_ids: list[str] | None = None


class GamePlugin(ABC):
    """A board-game plugin consumed by the common room framework."""

    game_type: str
    display_name: str
    rules_text: str
    move_format: str
    min_players: int = 2
    max_players: int = 2
    supports_stakes: bool = False
    # Future multiplayer games must opt in separately and return explicit
    # ``MoveResult.settlement_deltas``; the framework never invents a payout.
    supports_multiplayer_stakes: bool = False

    def public_state(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Return the state shared with every authenticated room participant.

        Public-board games inherit the identity projection. Games with hidden
        information must override this method and omit all hands, private rolls,
        and viewer-specific legal actions from the returned value.
        """
        return deepcopy(state)

    def private_state(
        self,
        state: dict[str, Any],
        viewer: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Return only the authenticated viewer's private state."""
        return {}

    def project_event(
        self,
        event: dict[str, Any],
        viewer: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Project one already visibility-authorized event for ``viewer``.

        Returning ``None`` hides the event. Hidden-information plugins should
        redact private move payloads here; the default preserves legacy public
        chess/checker-style events.
        """
        return deepcopy(event)

    def progress_after_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
        applied: dict[str, Any] | MoveResult,
    ) -> dict[str, Any] | MoveResult:
        """Optional phase/round progression hook, called once per valid action."""
        return applied

    def settlement_deltas(
        self,
        state: dict[str, Any],
        result: dict[str, Any],
        participants: list[dict[str, Any]],
        stake: int,
    ) -> dict[str, int] | None:
        """Return an explicit multiplayer payout, or ``None`` for no policy.

        This hook is consulted only for an opted-in multiplayer stake game.
        Framework validation requires every participant, integer deltas, and a
        zero total before any wallet mutation.
        """
        return None

    def initialize(self, participants: list[dict[str, Any]]) -> dict[str, Any]:
        """Initialize state with seat-aware participants available to new plugins.

        Existing two-player plugins keep implementing ``initial_state()`` and are
        adapted here without any game-specific changes.
        """
        return self.initial_state()

    def tokens_for(self, participants: list[dict[str, Any]]) -> list[str]:
        """Assign stable per-seat tokens; multiplayer plugins may override."""
        defaults = ["X", "O"]
        return [
            defaults[index] if index < len(defaults) else f"P{index + 1}"
            for index, _participant in enumerate(participants)
        ]

    def first_player_id(
        self, participants: list[dict[str, Any]], mode: str
    ) -> str:
        """Choose the opening seat; legacy modes retain human/AI-first behavior."""
        preferred_role = "human" if mode == "human_first" else "ai"
        preferred = next(
            (
                item for item in participants
                if item.get("role") == preferred_role and item.get("active", True)
            ),
            None,
        )
        if preferred is not None:
            return str(preferred["player_id"])
        active = next(
            (item for item in participants if item.get("active", True)), None
        )
        if active is None:
            raise ValueError("房间至少需要一名可行动参与者")
        return str(active["player_id"])

    def validate_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> None:
        self.validate_move(state, move, actor["token"])

    def apply_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> dict[str, Any] | MoveResult:
        return self.apply_move(state, move, actor["token"])

    def format_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> str:
        return self.format_move(state, move, actor["token"])

    def result_for(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Normalize the legacy X/O winner into a multiplayer-capable result."""
        outcome = self.check_winner(state)
        if outcome is None:
            return None
        if outcome == "draw":
            return {"draw": True}
        winner = next(
            (item for item in participants if item.get("token") == outcome),
            None,
        )
        if winner is None:
            raise ValueError("插件返回的赢家 token 不属于任何参与者")
        return {"winner_player_id": winner["player_id"], "draw": False}

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

    def format_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> str:
        row = move.get("row")
        col = move.get("col")
        if isinstance(row, int) and isinstance(col, int) and 0 <= col < 26:
            return f"{chr(ord('A') + col)}{row + 1}"
        return str(move)


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
