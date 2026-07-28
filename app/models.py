from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateRoomBody(StrictBody):
    player_id: str = Field(min_length=1, max_length=80)
    game_type: Literal["tictactoe", "gomoku"]
    mode: Literal["human_first", "ai_first"] = "human_first"


class JoinRoomBody(StrictBody):
    player_id: str = Field(min_length=1, max_length=80)


class MoveBody(StrictBody):
    player_id: str = Field(min_length=1, max_length=80)
    row: int
    col: int


class ResignBody(StrictBody):
    player_id: str = Field(min_length=1, max_length=80)


class McpPlayBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["new", "join", "move", "state", "resign"]
    player_id: str = Field(min_length=1, max_length=80)
    room_id: str | None = None
    game_type: Literal["tictactoe", "gomoku"] | None = None
    mode: Literal["human_first", "ai_first"] | None = None
    move: dict[str, Any] | None = None
    wait: bool = False

