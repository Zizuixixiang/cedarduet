from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MoveResult:
    """Optional metadata returned by plugins after a legal move."""

    state: dict[str, Any]
    retain_turn: bool = False
    pause_turn: bool = False
    note: str = ""
    next_player_id: str | None = None
    inactive_player_ids: list[str] = field(default_factory=list)
    skipped_player_ids: list[str] = field(default_factory=list)
    participant_activity: dict[str, str] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    settlement_deltas: dict[str, int] | None = None
    event_visible_to_player_ids: list[str] | None = None
    # Optional system-authored, room-public delta emitted once alongside this
    # action. Plugins must include only information already revealed to every
    # participant; private state belongs in ``private_state`` projections.
    public_event: dict[str, Any] | None = None


class GamePlugin(ABC):
    """A board-game plugin consumed by the common room framework."""

    game_type: str
    display_name: str
    category: str = "board"
    rules_text: str
    move_format: str
    min_players: int = 2
    max_players: int = 2
    # Legacy plugins derive an inclusive range. New plugins can declare an
    # explicit tuple when only discrete table sizes are valid.
    allowed_player_counts: tuple[int, ...] | None = None
    recommended_players: int = 2
    supports_npcs: bool = False
    supports_stakes: bool = False
    # Future multiplayer games must opt in separately and return explicit
    # ``MoveResult.settlement_deltas``; the framework never invents a payout.
    supports_multiplayer_stakes: bool = False
    # Most two-player games retain the framework's fixed +/- stake payout.
    # Games whose rules scale liabilities (for example by cards remaining)
    # explicitly opt into their settlement hook for two-player tables too.
    uses_custom_stake_settlement: bool = False

    def resolved_allowed_player_counts(self) -> tuple[int, ...]:
        raw = self.allowed_player_counts
        if raw is None:
            if (
                isinstance(self.min_players, bool)
                or isinstance(self.max_players, bool)
                or not isinstance(self.min_players, int)
                or not isinstance(self.max_players, int)
                or not 2 <= self.min_players <= self.max_players <= 6
            ):
                raise ValueError("游戏人数范围必须位于 2–6")
            counts = tuple(range(self.min_players, self.max_players + 1))
        else:
            if not isinstance(raw, (tuple, list, set, frozenset)) or not raw:
                raise ValueError("allowed_player_counts 必须是非空人数集合")
            if any(
                isinstance(count, bool)
                or not isinstance(count, int)
                or not 2 <= count <= 6
                for count in raw
            ):
                raise ValueError("allowed_player_counts 只能包含 2–6 的整数")
            counts = tuple(sorted(set(raw)))
        return counts

    def accepts_player_count(self, count: int) -> bool:
        return count in self.resolved_allowed_player_counts()

    def resolved_recommended_players(self) -> int:
        counts = self.resolved_allowed_player_counts()
        return (
            self.recommended_players
            if self.recommended_players in counts
            else counts[0]
        )

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

    def participant_summary(
        self,
        state: dict[str, Any],
        participant: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, str | int | bool | None]:
        """Return compact public metadata for one generic roster card.

        ``state`` is the already public projection, so hidden-information games
        cannot accidentally derive a roster badge from another player's hand.
        Plugins should keep this to a few scalar values such as score or dice
        count; the common web renderer owns the surrounding seat-card layout.
        """
        del state, participant, participants
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

    def npc_compact_rules(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> str:
        """Return concise rules safe to send to an NPC provider."""
        del state, actor, participants
        return self.rules_text

    def npc_public_actions(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Return already-public action history, never private event payloads."""
        del state, actor, participants
        return []

    def npc_legal_actions(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Return the authoritative legal moves for the current NPC actor."""
        del state, actor, participants
        return []

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

    def initialize_for_first_player(
        self,
        participants: list[dict[str, Any]],
        first_player_id: str,
    ) -> dict[str, Any]:
        """Initialize with the already resolved opener when rules need it.

        Existing games inherit the participant-only initialization path. Card
        games that deal an extra dealer card can override this hook without
        making the room framework reorder stable seats.
        """
        del first_player_id
        return self.initialize(participants)

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
