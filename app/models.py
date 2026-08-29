import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateRoomBody(StrictBody):
    player_id: str = Field(min_length=1, max_length=80)
    opponent_id: str | None = Field(default=None, min_length=1, max_length=80)
    ai_player: str | None = Field(default=None, min_length=1, max_length=80)
    ai_players: list[str] | None = Field(default=None, max_length=5)
    game_type: str = Field(min_length=1, max_length=40)
    mode: Literal["human_first", "ai_first", "random"] = "human_first"
    stake: int = Field(default=0, ge=0)
    target_player_count: int | None = Field(default=None, ge=2, le=6)
    fill_with_npcs: bool = False
    rematch_of_room_id: str | None = Field(default=None, min_length=8, max_length=8)

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
    revision: int | None = Field(default=None, ge=0)

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

    @field_validator("revision", mode="before")
    @classmethod
    def require_integer_revision(cls, value):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int)
        ):
            raise ValueError("revision 必须是非负整数")
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


class NotificationAckBody(StrictBody):
    category: Literal["game", "loan", "exchange", "achievement"]
    reference_id: str | None = Field(default=None, min_length=1, max_length=128)


class LoanTermsBody(StrictBody):
    principal: int = Field(gt=0)
    daily_rate_micro_percent: int = Field(ge=0, le=9_223_372_036_854_775_807)
    due_date: str = Field(min_length=10, max_length=10)
    interest_cap_enabled: bool = True
    idempotency_key: str = Field(min_length=8, max_length=128)

    @field_validator("principal", "daily_rate_micro_percent", mode="before")
    @classmethod
    def require_integer_loan_terms(cls, value):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("借款金额与利率必须使用整数单位")
        return value

    @field_validator("interest_cap_enabled", mode="before")
    @classmethod
    def require_boolean_cap(cls, value):
        if not isinstance(value, bool):
            raise ValueError("利息封顶保护必须是布尔值")
        return value


class LoanCreateBody(LoanTermsBody):
    machine_id: str = Field(min_length=1, max_length=80)


class LoanCounterBody(LoanTermsBody):
    revision: int = Field(ge=1)

    @field_validator("revision", mode="before")
    @classmethod
    def require_integer_revision(cls, value):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("revision 必须是正整数")
        return value


class LoanDecisionBody(StrictBody):
    revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=128)

    @field_validator("revision", mode="before")
    @classmethod
    def require_integer_revision(cls, value):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("revision 必须是正整数")
        return value


class LoanRepaymentBody(StrictBody):
    amount: int = Field(gt=0)
    idempotency_key: str = Field(min_length=8, max_length=128)

    @field_validator("amount", mode="before")
    @classmethod
    def require_integer_amount(cls, value):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("还款额必须是正整数")
        return value


class ExchangeCreateBody(StrictBody):
    machine_id: str = Field(min_length=1, max_length=80)
    item_key: str = Field(min_length=1, max_length=40)
    request_note: str = Field(min_length=1, max_length=120)
    chip_amount: int = Field(ge=1, le=100)
    custom_title: str | None = Field(default=None, max_length=30)
    idempotency_key: str = Field(min_length=8, max_length=128)

    @field_validator("chip_amount", mode="before")
    @classmethod
    def require_integer_chip_amount(cls, value):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("筹码数必须是 1-100 的整数")
        return value


class ExchangeDecisionBody(StrictBody):
    idempotency_key: str = Field(min_length=8, max_length=128)


class McpPlayBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal[
        "rooms", "new", "rematch", "join", "move", "state", "resign", "leave", "accept", "reject",
        "chips",
    ]
    player_id: str = Field(min_length=1, max_length=80)
    opponent_id: str | None = Field(default=None, min_length=1, max_length=80)
    participant_ids: list[str] | None = Field(default=None, min_length=1, max_length=6)
    room_id: str | None = None
    game_type: str | None = Field(default=None, min_length=1, max_length=40)
    mode: Literal["human_first", "ai_first"] | None = None
    move: dict[str, Any] | None = None
    wait: bool = False
    full_state: bool = False
    message: str | None = Field(default=None, max_length=500)
    include_terminal: bool = False
    limit: int | None = Field(default=None, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    stake: int = Field(default=0, ge=0)
    target_player_count: int | None = Field(default=None, ge=2, le=6)
    fill_with_npcs: bool = False
    op: Literal[
        "status", "check_in", "bankruptcy", "ledger", "achievements", "loans",
        "exchange",
    ] | None = None
    revision: int | None = Field(default=None, ge=0)
    loan_action: Literal[
        "list", "create", "accept", "reject", "counter", "withdraw", "repay"
    ] | None = None
    loan_id: str | None = Field(default=None, min_length=1, max_length=80)
    loan_revision: int | None = Field(default=None, ge=1)
    principal: int | None = Field(default=None, gt=0)
    daily_rate_micro_percent: int | None = Field(
        default=None, ge=0, le=9_223_372_036_854_775_807
    )
    due_date: str | None = Field(default=None, min_length=10, max_length=10)
    interest_cap_enabled: bool | None = None
    amount: int | None = Field(default=None, gt=0)
    exchange_action: Literal[
        "catalog", "list", "create", "confirm", "reject", "withdraw"
    ] | None = None
    request_id: str | None = Field(default=None, min_length=1, max_length=80)
    item_key: str | None = Field(default=None, min_length=1, max_length=40)
    request_note: str | None = Field(default=None, min_length=1, max_length=120)
    custom_title: str | None = Field(default=None, max_length=30)
    chip_amount: int | None = Field(default=None, ge=1, le=100)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)

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

    @field_validator("revision", mode="before")
    @classmethod
    def require_integer_revision(cls, value):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int)
        ):
            raise ValueError("revision 必须是非负整数")
        return value

    @field_validator(
        "loan_revision", "principal", "daily_rate_micro_percent", "amount",
        "chip_amount",
        mode="before",
    )
    @classmethod
    def require_integer_loan_fields(cls, value):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int)
        ):
            raise ValueError("revision、金额与利率必须使用整数")
        return value

    @field_validator("interest_cap_enabled", mode="before")
    @classmethod
    def require_boolean_loan_cap(cls, value):
        if value is not None and not isinstance(value, bool):
            raise ValueError("interest_cap_enabled 必须是布尔值")
        return value

    @field_validator("participant_ids")
    @classmethod
    def unique_participant_ids(cls, value):
        if value is not None and len(set(value)) != len(value):
            raise ValueError("participant_ids 不能包含重复参与者")
        return value
