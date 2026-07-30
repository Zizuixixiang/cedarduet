import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateRoomBody(StrictBody):
    player_id: str = Field(min_length=1, max_length=80)
    opponent_id: str | None = Field(default=None, min_length=1, max_length=80)
    ai_player: str | None = Field(default=None, min_length=1, max_length=80)
    game_type: str = Field(min_length=1, max_length=40)
    mode: Literal["human_first", "ai_first"] = "human_first"


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


class MessageBody(StrictBody):
    player_id: str = Field(min_length=1, max_length=80)
    opponent_id: str | None = Field(default=None, min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=500)


class McpPlayBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["new", "join", "move", "state", "resign"]
    player_id: str = Field(min_length=1, max_length=80)
    opponent_id: str | None = Field(default=None, min_length=1, max_length=80)
    room_id: str | None = None
    game_type: str | None = Field(default=None, min_length=1, max_length=40)
    mode: Literal["human_first", "ai_first"] | None = None
    move: dict[str, Any] | None = None
    wait: bool = False
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
