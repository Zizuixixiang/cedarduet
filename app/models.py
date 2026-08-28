import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateRoomBody(StrictBody):
    player_id: str = Field(min_length=1, max_length=80)
    opponent_id: str | None = Field(default=None, min_length=1, max_length=80)
    ai_player: str | None = Field(default=None, min_length=1, max_length=80)
    ai_players: list[str] | None = Field(default=None, max_length=3)
    game_type: str = Field(min_length=1, max_length=40)
    mode: Literal["human_first", "ai_first"] = "human_first"
    stake: int = Field(default=0, ge=0)

    @field_validator("stake", mode="before")
    @classmethod
    def require_integer_stake(cls, value):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("stake 必须是大于等于 0 的整数")
        return value

    @field_validator("ai_players")
    @classmethod
    def unique_ai_players(cls, value):
        if value is not None and len(set(value)) != len(value):
            raise ValueError("ai_players 不能包含重复小机")
        return value


class JoinRoomBody(StrictBody):
    player_id: str = Field(min_length=1, max_length=80)
    opponent_id: str | None = Field(default=None, min_length=1, max_length=80)
    message: str | None = Field(default=None, max_length=500)


class MoveBody(StrictBody):
    player_id: str = Field(min_length=1, max_length=80)
    opponent_id: str | None = Field(default=None, min_length=1, max_length=80)
    move: dict[str, Any] | None = None
    row: int | None = None
    col: int | None = None
    orientation: Literal["h", "v"] | None = None
    from_row: int | None = None
    from_col: int | None = None
    to_row: int | None = None
    to_col: int | None = None
    message: str | None = Field(default=None, max_length=500)

    @field_validator("move", mode="before")
    @classmethod
    def parse_string_move(cls, value):
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return value
            if isinstance(parsed, dict):
                return parsed
        return value


class ResignBody(StrictBody):
    player_id: str = Field(min_length=1, max_length=80)
    opponent_id: str | None = Field(default=None, min_length=1, max_length=80)
    message: str | None = Field(default=None, max_length=500)


class LeaveRoomBody(StrictBody):
    # The main-site proxy overwrites this with the authenticated human identity.
    player_id: str | None = Field(default=None, min_length=1, max_length=80)
    message: str | None = Field(default=None, max_length=500)


class MessageBody(StrictBody):
    player_id: str = Field(min_length=1, max_length=80)
    opponent_id: str | None = Field(default=None, min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=500)


class RoomRetentionBody(StrictBody):
    # The main-site proxy injects this for every human POST. Authorization still
    # relies exclusively on X-Duel-Human-Player in the backend.
    player_id: str | None = Field(default=None, min_length=1, max_length=80)
    preserved: bool


class RoomDeleteBody(StrictBody):
    player_id: str | None = Field(default=None, min_length=1, max_length=80)


class InvitationDecisionBody(StrictBody):
    player_id: str | None = Field(default=None, min_length=1, max_length=80)
    decision: Literal["accept", "reject"]


class McpPlayBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal[
        "rooms", "new", "join", "move", "state", "resign", "leave", "accept", "reject",
        "chips",
    ]
    player_id: str = Field(min_length=1, max_length=80)
    opponent_id: str | None = Field(default=None, min_length=1, max_length=80)
    participant_ids: list[str] | None = Field(default=None, min_length=1, max_length=4)
    room_id: str | None = None
    game_type: str | None = Field(default=None, min_length=1, max_length=40)
    mode: Literal["human_first", "ai_first"] | None = None
    move: dict[str, Any] | None = None
    wait: bool = False
    message: str | None = Field(default=None, max_length=500)
    include_terminal: bool = False
    limit: int | None = Field(default=None, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    stake: int = Field(default=0, ge=0)
    op: Literal["status", "check_in", "bankruptcy", "ledger"] | None = None

    @field_validator("move", mode="before")
    @classmethod
    def parse_string_move(cls, value):
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return value
            if isinstance(parsed, dict):
                return parsed
        return value

    @field_validator("wait", mode="before")
    @classmethod
    def parse_string_wait(cls, value):
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized == "true":
                return True
            if normalized == "false":
                return False
            raise ValueError("wait 字符串只能是 true 或 false")
        return value

    @field_validator("stake", mode="before")
    @classmethod
    def require_integer_stake(cls, value):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("stake 必须是大于等于 0 的整数")
        return value

    @field_validator("participant_ids")
    @classmethod
    def unique_participant_ids(cls, value):
        if value is not None and len(set(value)) != len(value):
            raise ValueError("participant_ids 不能包含重复参与者")
        return value
