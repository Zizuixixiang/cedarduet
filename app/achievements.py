"""Permanent, authority-derived achievements and automatic chip rewards.

Rooms are intentionally only an event source.  Every fact used below is copied
into additive achievement tables before a room can be deleted, and no
achievement table has a foreign key back to ``rooms``.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Literal
from zoneinfo import ZoneInfo

from .npc_personas import PersonaConfigError, load_personas

SubjectType = Literal["human", "ai"]
SHANGHAI = ZoneInfo("Asia/Shanghai")
PRODUCTION_NPC_NAMES = {
    "xu_zhi_heng": "许知衡",
    "yue_ming_chuan": "岳鸣川",
    "wen_xing_zhi": "温行止",
    "tang_yi": "唐熠",
    "shang_ling_yi": "商令仪",
    "qiao_mai": "乔麦",
}


@dataclass(frozen=True)
class AchievementDefinition:
    id: str
    name: str
    condition: str
    category: str
    subjects: tuple[SubjectType, ...]
    reward: int
    target: int = 1
    hidden: bool = False
    npc_persona_id: str | None = None


def _definition(
    achievement_id: str,
    name: str,
    condition: str,
    category: str,
    subjects: tuple[SubjectType, ...],
    reward: int,
    target: int = 1,
    *,
    hidden: bool = False,
    npc_persona_id: str | None = None,
) -> AchievementDefinition:
    return AchievementDefinition(
        achievement_id, name, condition, category, subjects, reward, target,
        hidden, npc_persona_id,
    )


# This is the single reward/catalog source of truth.  Hidden definitions are
# deliberately present here but are filtered before every public projection.
ACHIEVEMENT_CATALOG: tuple[AchievementDefinition, ...] = (
    _definition("first_normal_game", "落子无悔", "完成第一场正常对局", "common", ("human", "ai"), 5),
    _definition("first_authoritative_rematch", "再来一局", "完成第一次权威重赛", "common", ("human", "ai"), 5),
    _definition("first_normal_draw", "棋逢对手", "第一次正常和棋", "common", ("human", "ai"), 5),
    _definition("six_game_types", "十八般棋艺", "完成 6 种不同游戏", "common", ("human", "ai"), 20, 6),
    _definition("first_four_player_game", "满堂生辉", "完成第一场 4 人及以上正常对局", "common", ("human", "ai"), 10),
    _definition("first_staked_game", "愿赌服输", "完成第一场非 0 筹码局", "common", ("human", "ai"), 5),
    _definition("win_after_three_losses", "越挫越勇", "连续 3 场正常败局后取得胜利", "common", ("human", "ai"), 10, 3),
    _definition("win_gomoku", "五子登科", "赢得五子棋", "common", ("human", "ai"), 10),
    _definition("win_tictactoe", "井井有条", "赢得井字棋", "common", ("human", "ai"), 10),
    _definition("win_othello", "黑白分明", "赢得黑白棋", "common", ("human", "ai"), 10),
    _definition("win_connect4", "四通八达", "赢得四子连珠", "common", ("human", "ai"), 10),
    _definition("win_dots_boxes", "圈地为王", "赢得点格棋", "common", ("human", "ai"), 10),
    _definition("win_jungle", "万兽之王", "赢得斗兽棋", "common", ("human", "ai"), 10),
    _definition("first_negative_balance", "兜比脸干净", "筹码首次因实际交易或正常结算跌到 0 以下", "common", ("human", "ai"), 10),
    _definition("first_bankruptcy", "这下真没了", "第一次宣布破产", "common", ("human", "ai"), 5),
    _definition("bankruptcy_recovery", "东山再起", "宣布破产后余额恢复到不少于 200 并解除破产状态", "common", ("human", "ai"), 10),
    _definition("three_bankruptcies", "三起三落", "累计宣布破产 3 次", "common", ("human", "ai"), 20, 3),
    _definition("lose_100_in_game", "钱都去哪了", "单局正常筹码结算损失至少 100", "common", ("human", "ai"), 10, 100),
    _definition("win_zero_stake", "赢了也没钱", "第一次赢得 0 筹码局", "common", ("human", "ai"), 5),
    _definition("lose_zero_stake", "输了也不亏", "第一次输掉 0 筹码局", "common", ("human", "ai"), 5),
    _definition("ten_zero_stake_games", "君子之交", "累计完成 10 场 0 筹码局", "common", ("human", "ai"), 20, 10),
    _definition("five_zero_stake_same_opponent", "不押筹码，押一口气", "与同一对手连续完成 5 场 0 筹码局", "common", ("human", "ai"), 20, 5),

    _definition("human_rematch_after_loss", "人类的胜负欲", "人类输掉正常对局后主动发起权威重赛", "human", ("human",), 10),
    _definition("human_preserve_loss", "输了也要留档", "人类主动保留自己输掉的终局", "human", ("human",), 10),
    _definition("human_loses_to_bound_ai", "我家小机初长成", "第一次被自己的绑定小机正常击败", "human", ("human",), 10),

    _definition("ai_creates_room", "我自己来的", "小机第一次主动创建房间", "ai", ("ai",), 5),
    _definition("ai_beats_bound_human", "我不是陪玩", "小机第一次正常击败绑定人类", "ai", ("ai",), 10),
    _definition("ai_three_win_streak", "算力花在刀刃上", "小机取得 3 连胜", "ai", ("ai",), 10, 3),
    _definition("ai_authoritative_rematch", "轮到你了，人类", "小机第一次主动发起权威重赛", "ai", ("ai",), 10),
    _definition("ai_revenge_bound_human", "你教得好，下次别教了", "小机连续输给绑定人类 3 次后正常反胜", "ai", ("ai",), 20, 3),
    _definition("ai_six_game_types", "棋盘不在提示词里", "小机完成 6 种不同游戏", "ai", ("ai",), 20, 6),

    _definition("pair_first_game", "来都来了", "这对绑定人机完成第一局", "relationship", ("human", "ai"), 5),
    _definition("pair_ten_games", "又是你", "这对绑定人机完成 10 局", "relationship", ("human", "ai"), 10, 10),
    _definition("pair_fifty_games", "老对手了", "这对绑定人机完成 50 局", "relationship", ("human", "ai"), 20, 50),
    _definition("pair_reunion_after_seven_days", "座位还给你留着", "相隔至少 7 个完整上海自然日后再次正常同桌", "relationship", ("human", "ai"), 20, 7),
    _definition("pair_same_day_check_in", "同一天想起这里", "双方在同一上海自然日都完成签到", "relationship", ("human", "ai"), 10, 2),
    _definition("pair_both_won", "有来有回", "双方都曾正常战胜对方", "relationship", ("human", "ai"), 10, 2),
    _definition("pair_balanced_twenty", "半斤八两", "至少完成 20 场有胜负的对局，双方胜场差不超过 2", "relationship", ("human", "ai"), 20, 20),
    _definition("pair_five_wins_each", "相爱相杀", "双方各正常战胜对方至少 5 次", "relationship", ("human", "ai"), 20, 5),

    _definition("loan_first_borrower_active", "白纸黑字", "首次以借款人身份激活欠条", "loan", ("human", "ai"), 5),
    _definition("loan_first_lender_active", "江湖救急", "首次成功出借", "loan", ("human", "ai"), 5),
    _definition("loan_first_partial_repayment", "分期也是还", "首次部分还款后仍有余额", "loan", ("human", "ai"), 5),
    _definition("loan_first_ontime_repayment", "说到做到", "首次在到期日或之前还清", "loan", ("human", "ai"), 10),
    _definition("loan_three_ontime_repayments", "一诺千金", "累计 3 张欠条按时还清", "loan", ("human", "ai"), 20, 3),
    _definition("loan_lend_to_negative_borrower", "雪中送炭", "借款人负余额时成功出借", "loan", ("human", "ai"), 10),
    _definition("loan_debt_free_after_three", "无债一身轻", "同时有 3 张债务后全部还清", "loan", ("human", "ai"), 20),
    _definition("loan_pair_counter_activated", "有商有量", "同一对人机改条件后激活欠条", "relationship", ("human", "ai"), 5),
    _definition("loan_pair_bidirectional", "有来有往", "同一绑定组合双向借款均成功", "relationship", ("human", "ai"), 10, 2),

    _definition("jungle_rat_captures_elephant", "大象也怕老鼠", "斗兽棋中权威地用老鼠吃掉大象", "hidden", ("human", "ai"), 10, hidden=True),
    _definition("all_in_loss", "倾家荡产", "以开局正余额为筹码且押注额恰好等于余额，随后正常输掉", "hidden", ("human", "ai"), 20, hidden=True),
    _definition("settlement_balance_minus_500", "输到系统都心疼", "因正常对局结算使余额不高于 -500", "hidden", ("human", "ai"), 20, hidden=True),
    _definition("game_last_at_least_24h", "棋盘钉子户", "一场正常完成的对局从创建到结束至少 24 小时", "hidden", ("human", "ai"), 20, 24, hidden=True),
    _definition("ten_game_rematch_chain", "十局之后还是朋友", "与同一对手沿同一权威重赛链连续完成 10 局", "hidden", ("human", "ai"), 20, 10, hidden=True),
    _definition("non_tictactoe_draw", "这也能和？", "在非井字棋中达成正常和棋", "hidden", ("human", "ai"), 10, hidden=True),
    _definition("othello_win_both_sides", "黑白通吃", "黑白棋执不同阵营或先后手各正常赢过一次", "hidden", ("human", "ai"), 20, 2, hidden=True),
    # The authoritative data currently cannot prove a last-move deficit reversal.
    # Keeping the hidden definition without a trigger is safer than guessing.
    _definition("last_move_comeback_win", "一子定乾坤", "最后一手从落后反胜（等待权威局势证据）", "hidden", ("human", "ai"), 20, hidden=True),
    _definition("loan_three_active", "三张欠条一台戏", "同时有 3 张生效或逾期欠条", "hidden", ("human", "ai"), 10, 3, hidden=True),
    _definition("loan_first_overdue", "明日复明日", "首次发生欠条逾期", "hidden", ("human", "ai"), 0, hidden=True),
    _definition("loan_interest_cap_reached", "利息比本金还熟", "开启保护的欠条触达终身利息封顶", "hidden", ("human", "ai"), 0, hidden=True),

    _definition("defeat_npc_xu_zhi_heng", "这回算漏了", f"正常完成并战胜{PRODUCTION_NPC_NAMES['xu_zhi_heng']}", "npc", ("human", "ai"), 10, npc_persona_id="xu_zhi_heng"),
    _definition("defeat_npc_yue_ming_chuan", "别催，赢着呢", f"正常完成并战胜{PRODUCTION_NPC_NAMES['yue_ming_chuan']}", "npc", ("human", "ai"), 10, npc_persona_id="yue_ming_chuan"),
    _definition("defeat_npc_wen_xing_zhi", "这次没上当", f"正常完成并战胜{PRODUCTION_NPC_NAMES['wen_xing_zhi']}", "npc", ("human", "ai"), 10, npc_persona_id="wen_xing_zhi"),
    _definition("defeat_npc_tang_yi", "这句还给你", f"正常完成并战胜{PRODUCTION_NPC_NAMES['tang_yi']}", "npc", ("human", "ai"), 10, npc_persona_id="tang_yi"),
    _definition("defeat_npc_shang_ling_yi", "后发也有来不及", f"正常完成并战胜{PRODUCTION_NPC_NAMES['shang_ling_yi']}", "npc", ("human", "ai"), 10, npc_persona_id="shang_ling_yi"),
    _definition("defeat_npc_qiao_mai", "这次猜错啦", f"正常完成并战胜{PRODUCTION_NPC_NAMES['qiao_mai']}", "npc", ("human", "ai"), 10, npc_persona_id="qiao_mai"),
    _definition("defeat_all_six_npcs", "一个都没放过", "正常战胜全部六位生产 NPC", "npc", ("human", "ai"), 30, 6),
)

DEFINITIONS = {item.id: item for item in ACHIEVEMENT_CATALOG}
PRODUCTION_NPC_IDS = tuple(PRODUCTION_NPC_NAMES)
GAME_WIN_IDS = {
    "gomoku": "win_gomoku", "tictactoe": "win_tictactoe",
    "othello": "win_othello", "connect4": "win_connect4",
    "dots_boxes": "win_dots_boxes", "jungle": "win_jungle",
}
NPC_ACHIEVEMENT_IDS = {
    definition.npc_persona_id: definition.id
    for definition in ACHIEVEMENT_CATALOG
    if definition.npc_persona_id is not None
}


SCHEMA = """
CREATE TABLE IF NOT EXISTS achievement_room_openings (
    room_id TEXT NOT NULL,
    player_id TEXT NOT NULL,
    subject_type TEXT CHECK (subject_type IN ('human', 'ai') OR subject_type IS NULL),
    subject_id TEXT,
    participant_kind TEXT NOT NULL,
    npc_persona_id TEXT,
    opening_balance INTEGER,
    created_at TEXT NOT NULL,
    PRIMARY KEY (room_id, player_id)
);
CREATE TABLE IF NOT EXISTS achievement_matches (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id TEXT NOT NULL UNIQUE,
    game_type TEXT NOT NULL,
    stake INTEGER NOT NULL,
    initiator_player_id TEXT,
    rematch_of_room_id TEXT,
    rematch_root_room_id TEXT,
    created_at TEXT NOT NULL,
    terminal_at TEXT NOT NULL,
    terminal_reason TEXT NOT NULL,
    normal_outcome INTEGER NOT NULL CHECK (normal_outcome IN (0, 1)),
    participant_count INTEGER NOT NULL,
    winner_player_id TEXT,
    is_draw INTEGER NOT NULL CHECK (is_draw IN (0, 1)),
    result_json TEXT NOT NULL DEFAULT '{}',
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS achievement_match_participants (
    room_id TEXT NOT NULL REFERENCES achievement_matches(room_id) ON DELETE CASCADE,
    player_id TEXT NOT NULL,
    subject_type TEXT CHECK (subject_type IN ('human', 'ai') OR subject_type IS NULL),
    subject_id TEXT,
    participant_kind TEXT NOT NULL,
    npc_persona_id TEXT,
    seat_index INTEGER NOT NULL,
    token TEXT,
    outcome TEXT NOT NULL CHECK (outcome IN ('win', 'loss', 'draw', 'none')),
    chip_delta INTEGER,
    opening_balance INTEGER,
    balance_after INTEGER,
    PRIMARY KEY (room_id, player_id)
);
CREATE TABLE IF NOT EXISTS achievement_events (
    idempotency_key TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    subject_type TEXT CHECK (subject_type IN ('human', 'ai') OR subject_type IS NULL),
    subject_id TEXT,
    human_id TEXT,
    ai_id TEXT,
    room_id TEXT,
    effective_date TEXT,
    data_json TEXT NOT NULL DEFAULT '{}',
    occurred_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS achievement_relationships (
    human_id TEXT NOT NULL,
    ai_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (human_id, ai_id)
);
CREATE TABLE IF NOT EXISTS achievement_progress (
    subject_type TEXT NOT NULL CHECK (subject_type IN ('human', 'ai')),
    subject_id TEXT NOT NULL,
    achievement_id TEXT NOT NULL,
    context_key TEXT NOT NULL DEFAULT '',
    current_value INTEGER NOT NULL DEFAULT 0,
    target_value INTEGER NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (subject_type, subject_id, achievement_id, context_key)
);
CREATE TABLE IF NOT EXISTS achievement_unlocks (
    subject_type TEXT NOT NULL CHECK (subject_type IN ('human', 'ai')),
    subject_id TEXT NOT NULL,
    achievement_id TEXT NOT NULL,
    context_key TEXT NOT NULL DEFAULT '',
    reward INTEGER NOT NULL,
    unlocked_at TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT,
    PRIMARY KEY (subject_type, subject_id, achievement_id, context_key)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _pair_key(human_id: str, ai_id: str) -> str:
    return f"{human_id}|{ai_id}"


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _shanghai_date(value: str):
    return _parse_time(value).astimezone(SHANGHAI).date()


def init_achievement_schema(conn: sqlite3.Connection) -> None:
    """Create the additive schema and indexes; safe on every startup."""
    conn.executescript(SCHEMA)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_achievement_matches_normal_time "
        "ON achievement_matches(normal_outcome, terminal_at, room_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_achievement_participants_subject "
        "ON achievement_match_participants(subject_type, subject_id, room_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_achievement_participants_npc "
        "ON achievement_match_participants(npc_persona_id, room_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_achievement_events_subject "
        "ON achievement_events(subject_type, subject_id, event_type, occurred_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_achievement_events_checkin_date "
        "ON achievement_events(subject_type, subject_id, event_type, effective_date)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_achievement_unlocks_subject "
        "ON achievement_unlocks(subject_type, subject_id, unlocked_at)"
    )


def _wallet_subject(participant: dict) -> tuple[SubjectType, str] | None:
    kind = participant.get("participant_kind")
    if kind == "human":
        return "human", participant["player_id"]
    if kind == "bound_machine":
        return "ai", participant["player_id"]
    return None


def _register_pair(conn: sqlite3.Connection, human_id: str, ai_id: str) -> None:
    conn.execute(
        """
        INSERT INTO achievement_relationships (human_id, ai_id, created_at)
        VALUES (?, ?, ?) ON CONFLICT(human_id, ai_id) DO NOTHING
        """,
        (human_id, ai_id, _now()),
    )


def _record_creation_fact(conn: sqlite3.Connection, room: dict) -> list[dict]:
    initiator = next(
        (
            participant for participant in room.get("participants", [])
            if participant["player_id"] == room.get("initiator_player_id")
        ),
        None,
    )
    subject = _wallet_subject(initiator) if initiator else None
    if subject is None:
        return []
    subject_type, subject_id = subject
    event_type = "rematch_created" if room.get("rematch_of_room_id") else "room_created"
    timestamp = room.get("created_at") or _now()
    conn.execute(
        """
        INSERT INTO achievement_events (
            idempotency_key, event_type, subject_type, subject_id, room_id,
            data_json, occurred_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(idempotency_key) DO NOTHING
        """,
        (
            f"{event_type}:{room['room_id']}", event_type, subject_type,
            subject_id, room["room_id"],
            json.dumps(
                {"rematch_of_room_id": room.get("rematch_of_room_id")},
                ensure_ascii=False, separators=(",", ":"),
            ),
            timestamp,
        ),
    )
    return evaluate_subject(
        conn, subject_type, subject_id,
        source_type=event_type, source_id=room["room_id"],
    )


def record_room_created(conn: sqlite3.Connection, room: dict) -> list[dict]:
    """Persist trusted identities/opening balances and creation/rematch facts."""
    from .chips import INITIAL_BALANCE, _wallet_row

    timestamp = room.get("created_at") or _now()
    humans: list[str] = []
    ais: list[str] = []
    for participant in room.get("participants", []):
        subject = _wallet_subject(participant)
        opening_balance = None
        subject_type = subject_id = None
        if subject is not None:
            subject_type, subject_id = subject
            wallet = _wallet_row(conn, subject_type, subject_id)
            opening_balance = wallet["balance"] if wallet else INITIAL_BALANCE
            (humans if subject_type == "human" else ais).append(subject_id)
        conn.execute(
            """
            INSERT INTO achievement_room_openings (
                room_id, player_id, subject_type, subject_id, participant_kind,
                npc_persona_id, opening_balance, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(room_id, player_id) DO NOTHING
            """,
            (
                room["room_id"], participant["player_id"], subject_type, subject_id,
                participant.get("participant_kind") or "bound_machine",
                participant.get("npc_persona_id"), opening_balance, timestamp,
            ),
        )
    for human_id in humans:
        for ai_id in ais:
            _register_pair(conn, human_id, ai_id)

    return _record_creation_fact(conn, room)


def validate_rematch(
    conn: sqlite3.Connection,
    previous_room_id: str,
    participant_ids: Iterable[str],
    game_type: str,
) -> tuple[str, str]:
    """Return (previous, root) only for a completed, same-roster normal game."""
    previous = conn.execute(
        "SELECT * FROM achievement_matches WHERE room_id = ? AND normal_outcome = 1",
        (previous_room_id,),
    ).fetchone()
    if previous is None:
        raise ValueError("权威重赛只能接在已正常完成的对局之后")
    if previous["game_type"] != game_type:
        raise ValueError("权威重赛必须沿用同一游戏")
    old_ids = {
        row["player_id"] for row in conn.execute(
            "SELECT player_id FROM achievement_match_participants WHERE room_id = ?",
            (previous_room_id,),
        )
    }
    if old_ids != set(participant_ids):
        raise ValueError("权威重赛必须沿用同一批参与者")
    return previous_room_id, previous["rematch_root_room_id"] or previous_room_id


def _participant_delta(room: dict, participant: dict) -> int | None:
    deltas = (room.get("result") or {}).get("settlement_deltas")
    if isinstance(deltas, dict) and participant["player_id"] in deltas:
        value = deltas[participant["player_id"]]
        return value if isinstance(value, int) and not isinstance(value, bool) else None
    stake = room.get("stake", 0)
    if not stake or len(room.get("participants", [])) != 2 or room.get("winner") == "draw":
        return 0
    return stake if participant["player_id"] == room.get("winner_player_id") else -stake


def record_terminal_room(
    conn: sqlite3.Connection,
    room: dict,
    terminal_reason: str,
    *,
    normal: bool,
) -> list[dict]:
    """Copy one terminal room and evaluate every wallet subject exactly once."""
    inserted = conn.execute(
        """
        INSERT INTO achievement_matches (
            room_id, game_type, stake, initiator_player_id, rematch_of_room_id,
            rematch_root_room_id, created_at, terminal_at, terminal_reason,
            normal_outcome, participant_count, winner_player_id, is_draw,
            result_json, recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(room_id) DO NOTHING
        """,
        (
            room["room_id"], room["game_type"], room.get("stake", 0),
            room.get("initiator_player_id"), room.get("rematch_of_room_id"),
            room.get("rematch_root_room_id"), room["created_at"],
            room.get("terminal_at") or room.get("updated_at") or _now(),
            terminal_reason, int(normal), len(room.get("participants", [])),
            room.get("winner_player_id"), int(room.get("winner") == "draw"),
            json.dumps(room.get("result") or {}, ensure_ascii=False, separators=(",", ":")),
            _now(),
        ),
    ).rowcount
    if not inserted:
        return []

    opening_rows = {
        row["player_id"]: row for row in conn.execute(
            "SELECT * FROM achievement_room_openings WHERE room_id = ?",
            (room["room_id"],),
        )
    }
    proven_settlement = bool(opening_rows) or room.get("stake", 0) == 0 or (
        conn.execute(
            "SELECT 1 FROM chip_settlement_batches WHERE reference_type = 'duel_room' AND reference_id = ?",
            (room["room_id"],),
        ).fetchone() is not None
    )
    wallet_subjects: list[tuple[SubjectType, str]] = []
    humans: list[str] = []
    ais: list[str] = []
    for participant in room.get("participants", []):
        subject = _wallet_subject(participant)
        subject_type = subject_id = None
        balance_after = None
        if subject is not None:
            from .chips import _ensure_wallet, _wallet_row
            subject_type, subject_id = subject
            wallet = _wallet_row(conn, subject_type, subject_id)
            if normal and wallet is None:
                wallet = _ensure_wallet(conn, subject_type, subject_id)
            if wallet is not None and participant["player_id"] in opening_rows:
                # Runtime finalization is in the same transaction as settlement.
                balance_after = wallet["balance"]
            elif wallet is not None:
                # Legacy current balance is not a historical fact.  Only an
                # existing room-referenced settlement ledger can prove it.
                historical = conn.execute(
                    """
                    SELECT balance_after FROM chip_ledger
                    WHERE wallet_id = ? AND reference_type = 'duel_room'
                      AND reference_id = ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (wallet["id"], room["room_id"]),
                ).fetchone()
                balance_after = historical["balance_after"] if historical else None
            wallet_subjects.append(subject)
            (humans if subject_type == "human" else ais).append(subject_id)
        if room.get("winner") == "draw":
            outcome = "draw"
        elif room.get("winner_player_id"):
            outcome = "win" if participant["player_id"] == room["winner_player_id"] else "loss"
        else:
            outcome = "none"
        opening = opening_rows.get(participant["player_id"])
        conn.execute(
            """
            INSERT INTO achievement_match_participants (
                room_id, player_id, subject_type, subject_id, participant_kind,
                npc_persona_id, seat_index, token, outcome, chip_delta,
                opening_balance, balance_after
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                room["room_id"], participant["player_id"], subject_type, subject_id,
                participant.get("participant_kind") or "bound_machine",
                participant.get("npc_persona_id"), participant.get("seat_index", 0),
                participant.get("token"), outcome,
                _participant_delta(room, participant) if proven_settlement else None,
                opening["opening_balance"] if opening else None, balance_after,
            ),
        )
    for human_id in humans:
        for ai_id in ais:
            _register_pair(conn, human_id, ai_id)

    if not normal:
        return []
    unlocks: list[dict] = []
    for subject_type, subject_id in wallet_subjects:
        unlocks.extend(evaluate_subject(conn, subject_type, subject_id, source_type="duel_room", source_id=room["room_id"]))
    for human_id in humans:
        for ai_id in ais:
            unlocks.extend(evaluate_relationship(conn, human_id, ai_id, source_type="duel_room", source_id=room["room_id"]))
    return unlocks


def record_special_move(
    conn: sqlite3.Connection,
    room: dict,
    actor: dict,
    move: dict,
) -> list[dict]:
    """Persist only special move facts that the game engine proves directly."""
    if room.get("game_type") != "jungle":
        return []
    last_move = room.get("board_state", {}).get("last_move") or {}
    captured = last_move.get("captured")
    target_row, target_col = last_move.get("to_row"), last_move.get("to_col")
    board = room.get("board_state", {}).get("board") or []
    try:
        landed = board[target_row][target_col]
    except (IndexError, TypeError):
        return []
    if not (
        isinstance(captured, str) and captured.endswith(":E")
        and isinstance(landed, str) and landed.endswith(":R")
        and last_move.get("mark") == actor.get("token")
    ):
        return []
    subject = _wallet_subject(actor)
    if subject is None:
        return []
    subject_type, subject_id = subject
    key = f"jungle_rat_captures_elephant:{room['room_id']}:{room['revision']}"
    conn.execute(
        """
        INSERT INTO achievement_events (
            idempotency_key, event_type, subject_type, subject_id, room_id,
            data_json, occurred_at
        ) VALUES (?, 'jungle_rat_captures_elephant', ?, ?, ?, ?, ?)
        ON CONFLICT(idempotency_key) DO NOTHING
        """,
        (key, subject_type, subject_id, room["room_id"], json.dumps(move, separators=(",", ":")), _now()),
    )
    return evaluate_subject(conn, subject_type, subject_id, source_type="move", source_id=room["room_id"])


def record_preserved_loss(
    conn: sqlite3.Connection, human_id: str, room_id: str
) -> list[dict]:
    loss = conn.execute(
        """
        SELECT 1 FROM achievement_matches AS match
        JOIN achievement_match_participants AS participant USING (room_id)
        WHERE match.room_id = ? AND match.normal_outcome = 1
          AND participant.subject_type = 'human' AND participant.subject_id = ?
          AND participant.outcome = 'loss'
        """,
        (room_id, human_id),
    ).fetchone()
    if loss is None:
        return []
    conn.execute(
        """
        INSERT INTO achievement_events (
            idempotency_key, event_type, subject_type, subject_id, room_id, occurred_at
        ) VALUES (?, 'preserved_loss', 'human', ?, ?, ?)
        ON CONFLICT(idempotency_key) DO NOTHING
        """,
        (f"preserved_loss:{room_id}:{human_id}", human_id, room_id, _now()),
    )
    return evaluate_subject(conn, "human", human_id, source_type="preserve", source_id=room_id)


def record_check_in(
    conn: sqlite3.Connection,
    subject_type: SubjectType,
    subject_id: str,
    effective_date: str,
    *,
    human_ids: Iterable[str] = (),
    ai_ids: Iterable[str] = (),
) -> list[dict]:
    conn.execute(
        """
        INSERT INTO achievement_events (
            idempotency_key, event_type, subject_type, subject_id,
            effective_date, occurred_at
        ) VALUES (?, 'check_in', ?, ?, ?, ?)
        ON CONFLICT(idempotency_key) DO NOTHING
        """,
        (f"check_in:{subject_type}:{subject_id}:{effective_date}", subject_type, subject_id, effective_date, _now()),
    )
    pairs = {(human_id, ai_id) for human_id in human_ids for ai_id in ai_ids}
    unlocks = evaluate_subject(conn, subject_type, subject_id, source_type="check_in", source_id=effective_date)
    for human_id, ai_id in pairs:
        _register_pair(conn, human_id, ai_id)
        unlocks.extend(evaluate_relationship(conn, human_id, ai_id, source_type="check_in", source_id=effective_date))
    return unlocks


def record_bankruptcy(
    conn: sqlite3.Connection,
    subject_type: SubjectType,
    subject_id: str,
    bankruptcy_count: int,
) -> list[dict]:
    conn.execute(
        """
        INSERT INTO achievement_events (
            idempotency_key, event_type, subject_type, subject_id,
            data_json, occurred_at
        ) VALUES (?, 'bankruptcy', ?, ?, ?, ?)
        ON CONFLICT(idempotency_key) DO NOTHING
        """,
        (
            f"bankruptcy:{subject_type}:{subject_id}:{bankruptcy_count}", subject_type,
            subject_id, json.dumps({"count": bankruptcy_count}), _now(),
        ),
    )
    return evaluate_subject(conn, subject_type, subject_id, source_type="bankruptcy", source_id=str(bankruptcy_count))


def record_wallet_change(
    conn: sqlite3.Connection,
    subject_type: SubjectType,
    subject_id: str,
    *,
    transaction_type: str,
    reference_type: str | None,
    reference_id: str | None,
    idempotency_key: str | None,
    balance_after: int,
) -> list[dict]:
    """Record non-room authoritative chip changes without treating test/admin deltas as facts."""
    authoritative_types = {"chip_transfer", "chip_trade", "loan_repayment"}
    if transaction_type not in authoritative_types or reference_type is None:
        # Recovery itself may result from any real ledger grant, including check-in
        # and achievement rewards; the bankruptcy count/badge guards false unlocks.
        return evaluate_subject(conn, subject_type, subject_id, source_type="wallet", source_id=reference_id)
    key = idempotency_key or f"{transaction_type}:{reference_type}:{reference_id}:{subject_type}:{subject_id}"
    conn.execute(
        """
        INSERT INTO achievement_events (
            idempotency_key, event_type, subject_type, subject_id, data_json, occurred_at
        ) VALUES (?, 'authoritative_chip_change', ?, ?, ?, ?)
        ON CONFLICT(idempotency_key) DO NOTHING
        """,
        (f"achievement:{key}", subject_type, subject_id, json.dumps({"balance_after": balance_after}), _now()),
    )
    return evaluate_subject(conn, subject_type, subject_id, source_type="wallet", source_id=reference_id)


def record_loan_event(
    conn: sqlite3.Connection,
    loan: sqlite3.Row,
    event_type: str,
    *,
    event_id: str,
    data: dict | None = None,
) -> list[dict]:
    """Record only authoritative facts emitted by the loan service."""
    if not conn.in_transaction:
        raise RuntimeError("loan achievement facts require a write transaction")
    allowed = {
        "loan_activated", "loan_partial_repayment", "loan_repaid",
        "loan_debt_free", "loan_overdue", "loan_interest_cap_reached",
    }
    if event_type not in allowed:
        raise ValueError("unsupported loan achievement event")
    _register_pair(conn, loan["human_id"], loan["ai_id"])
    fact = {
        "loan_id": loan["loan_id"],
        "borrower_type": loan["borrower_type"],
        "borrower_id": loan["borrower_id"],
        "lender_type": loan["lender_type"],
        "lender_id": loan["lender_id"],
        **(data or {}),
    }
    if event_type == "loan_activated":
        subjects = [
            (loan["borrower_type"], loan["borrower_id"], "loan_activated_borrower"),
            (loan["lender_type"], loan["lender_id"], "loan_activated_lender"),
        ]
    else:
        subjects = [(loan["borrower_type"], loan["borrower_id"], event_type)]
    for subject_type, subject_id, stored_type in subjects:
        conn.execute(
            """
            INSERT INTO achievement_events (
                idempotency_key, event_type, subject_type, subject_id,
                human_id, ai_id, data_json, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(idempotency_key) DO NOTHING
            """,
            (
                f"loan:{stored_type}:{event_id}:{subject_type}:{subject_id}",
                stored_type, subject_type, subject_id,
                loan["human_id"], loan["ai_id"],
                json.dumps(fact, ensure_ascii=False, separators=(",", ":")), _now(),
            ),
        )
    unlocks: list[dict] = []
    for subject_type, subject_id in (
        (loan["borrower_type"], loan["borrower_id"]),
        (loan["lender_type"], loan["lender_id"]),
    ):
        unlocks.extend(
            evaluate_subject(
                conn, subject_type, subject_id,
                source_type=event_type, source_id=loan["loan_id"],
            )
        )
    unlocks.extend(
        evaluate_relationship(
            conn, loan["human_id"], loan["ai_id"],
            source_type=event_type, source_id=loan["loan_id"],
        )
    )
    return unlocks


def _normal_rows(conn: sqlite3.Connection, subject_type: SubjectType, subject_id: str) -> list[dict]:
    return [
        dict(row) for row in conn.execute(
            """
            SELECT match.*, participant.outcome, participant.chip_delta,
                   participant.opening_balance, participant.balance_after,
                   participant.token
            FROM achievement_matches AS match
            JOIN achievement_match_participants AS participant USING (room_id)
            WHERE match.normal_outcome = 1
              AND participant.subject_type = ? AND participant.subject_id = ?
            ORDER BY match.sequence
            """,
            (subject_type, subject_id),
        )
    ]


def _event_count(conn: sqlite3.Connection, subject_type: SubjectType, subject_id: str, event_type: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM achievement_events WHERE subject_type = ? AND subject_id = ? AND event_type = ?",
        (subject_type, subject_id, event_type),
    ).fetchone()[0]


def _streak_state(rows: list[dict], desired: str) -> tuple[int, int]:
    best = current = 0
    for row in rows:
        if row["outcome"] == desired:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return current, best


def _losses_before_win(rows: list[dict]) -> tuple[int, bool]:
    losses = 0
    achieved = False
    for row in rows:
        if row["outcome"] == "loss":
            losses += 1
        elif row["outcome"] == "win":
            achieved = achieved or losses >= 3
            losses = 0
        else:
            losses = 0
    return min(losses, 3), achieved


def _opponents_by_room(
    conn: sqlite3.Connection,
    subject_type: SubjectType,
    subject_id: str,
) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for row in conn.execute(
        """
        SELECT opponent.*
        FROM achievement_matches AS match
        JOIN achievement_match_participants AS subject
          ON subject.room_id = match.room_id
        JOIN achievement_match_participants AS opponent
          ON opponent.room_id = match.room_id
         AND opponent.player_id <> subject.player_id
        WHERE match.normal_outcome = 1
          AND subject.subject_type = ? AND subject.subject_id = ?
        ORDER BY match.sequence, opponent.seat_index
        """,
        (subject_type, subject_id),
    ):
        result.setdefault(row["room_id"], []).append(dict(row))
    return result


def _zero_stake_opponent_streak(
    rows: list[dict], opponents: dict[str, list[dict]]
) -> tuple[int, int]:
    current_id = None
    current = best = 0
    for row in rows:
        others = opponents.get(row["room_id"], [])
        if row["stake"] == 0 and row["participant_count"] == 2 and len(others) == 1:
            opponent_id = others[0]["player_id"]
            current = current + 1 if opponent_id == current_id else 1
            current_id = opponent_id
            best = max(best, current)
        else:
            current_id, current = None, 0
    return current, best


def _max_rematch_chain(rows: list[dict], opponents: dict[str, list[dict]]) -> int:
    lengths: dict[str, int] = {}
    opponent_ids: dict[str, str] = {}
    game_types: dict[str, str] = {}
    best = 0
    for row in rows:
        others = opponents.get(row["room_id"], [])
        if row["participant_count"] != 2 or len(others) != 1:
            lengths[row["room_id"]] = 0
            continue
        opponent_id = others[0]["player_id"]
        previous_id = row.get("rematch_of_room_id")
        if previous_id is None:
            length = 1
        elif (
            lengths.get(previous_id, 0) > 0
            and opponent_ids.get(previous_id) == opponent_id
            and game_types.get(previous_id) == row["game_type"]
        ):
            length = lengths[previous_id] + 1
        else:
            # A missing or mismatched predecessor is not a provable chain.
            length = 0
        lengths[row["room_id"]] = length
        opponent_ids[row["room_id"]] = opponent_id
        game_types[row["room_id"]] = row["game_type"]
        best = max(best, length)
    return best


def _ai_revenge_state(
    rows: list[dict], opponents: dict[str, list[dict]]
) -> tuple[int, bool]:
    current_human_id = None
    current_losses = 0
    achieved = False
    for row in rows:
        humans = {
            opponent["player_id"]
            for opponent in opponents.get(row["room_id"], [])
            if opponent.get("participant_kind") == "human"
        }
        winner_id = row.get("winner_player_id")
        if row["outcome"] == "loss" and winner_id in humans:
            if winner_id == current_human_id:
                current_losses += 1
            else:
                current_human_id = winner_id
                current_losses = 1
            continue
        if (
            row["outcome"] == "win"
            and current_human_id in humans
            and current_losses >= 3
        ):
            achieved = True
        current_human_id = None
        current_losses = 0
    return min(current_losses, 3), achieved


def _defeated_npcs(rows: list[dict], opponents: dict[str, list[dict]]) -> set[str]:
    return {
        opponent["npc_persona_id"]
        for row in rows if row["outcome"] == "win"
        for opponent in opponents.get(row["room_id"], [])
        if opponent.get("participant_kind") == "system_npc" and opponent.get("npc_persona_id")
    }


def _progress(
    conn: sqlite3.Connection,
    subject_type: SubjectType,
    subject_id: str,
    definition: AchievementDefinition,
    current: int,
    *,
    context_key: str = "",
    detail: dict | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO achievement_progress (
            subject_type, subject_id, achievement_id, context_key,
            current_value, target_value, detail_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(subject_type, subject_id, achievement_id, context_key)
        DO UPDATE SET current_value = excluded.current_value,
                      target_value = excluded.target_value,
                      detail_json = excluded.detail_json,
                      updated_at = excluded.updated_at
        """,
        (
            subject_type, subject_id, definition.id, context_key,
            max(0, int(current)), definition.target,
            json.dumps(detail or {}, ensure_ascii=False, separators=(",", ":")), _now(),
        ),
    )


def _unlock(
    conn: sqlite3.Connection,
    subject_type: SubjectType,
    subject_id: str,
    achievement_id: str,
    *,
    context_key: str = "",
    source_type: str,
    source_id: str | None,
) -> dict | None:
    from .chips import _apply_balance_change, _ensure_wallet

    if not conn.in_transaction:
        raise RuntimeError("achievement unlocks require an explicit write transaction")
    definition = DEFINITIONS[achievement_id]
    unlocked_at = _now()
    inserted = conn.execute(
        """
        INSERT INTO achievement_unlocks (
            subject_type, subject_id, achievement_id, context_key,
            reward, unlocked_at, source_type, source_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(subject_type, subject_id, achievement_id, context_key) DO NOTHING
        """,
        (
            subject_type, subject_id, achievement_id, context_key,
            definition.reward, unlocked_at, source_type, source_id,
        ),
    ).rowcount
    if not inserted:
        return None
    if definition.reward > 0:
        wallet = _ensure_wallet(conn, subject_type, subject_id)
        _apply_balance_change(
            conn, wallet, definition.reward, "achievement_reward",
            idempotency_key=f"achievement:{achievement_id}:{context_key or 'global'}",
            reference_type="achievement", reference_id=achievement_id,
            metadata={"achievement_name": definition.name, "context_key": context_key},
        )
    from .notifications import create_notification

    create_notification(
        conn,
        subject_type,
        subject_id,
        "achievement",
        "unlocked",
        achievement_id,
        (
            f"成就「{definition.name}」已解锁，奖励 {definition.reward} 筹码"
            if definition.reward > 0
            else f"成就「{definition.name}」已解锁"
        ),
        event_key=f"achievement:unlocked:{achievement_id}:{context_key or 'global'}",
        created_at=unlocked_at,
    )
    payload = {
        "subject_type": subject_type,
        "subject_id": subject_id,
        "id": achievement_id,
        "name": definition.name,
        "reward": definition.reward,
        "unlocked_at": unlocked_at,
    }
    if context_key:
        payload["context_key"] = context_key
    return payload


def _refresh_bankruptcy_recovery(
    conn: sqlite3.Connection,
    subject_type: SubjectType,
    subject_id: str,
    *,
    source_type: str,
    source_id: str | None,
) -> list[dict]:
    wallet = conn.execute(
        """
        SELECT balance, bankruptcy_count, bankruptcy_badge_active
        FROM chip_wallets WHERE subject_type = ? AND subject_id = ?
        """,
        (subject_type, subject_id),
    ).fetchone()
    recovered = bool(
        wallet and wallet["bankruptcy_count"] > 0
        and wallet["balance"] >= 200
        and not wallet["bankruptcy_badge_active"]
    )
    definition = DEFINITIONS["bankruptcy_recovery"]
    _progress(conn, subject_type, subject_id, definition, int(recovered))
    if not recovered:
        return []
    unlocked = _unlock(
        conn, subject_type, subject_id, "bankruptcy_recovery",
        source_type=source_type, source_id=source_id,
    )
    return [unlocked] if unlocked else []


def evaluate_subject(
    conn: sqlite3.Connection,
    subject_type: SubjectType,
    subject_id: str,
    *,
    source_type: str,
    source_id: str | None,
) -> list[dict]:
    rows = _normal_rows(conn, subject_type, subject_id)
    opponents = _opponents_by_room(conn, subject_type, subject_id)
    total = len(rows)
    wins = [row for row in rows if row["outcome"] == "win"]
    losses = [row for row in rows if row["outcome"] == "loss"]
    draws = [row for row in rows if row["outcome"] == "draw"]
    game_types = {row["game_type"] for row in rows}
    loss_progress, comeback = _losses_before_win(rows)
    defeated_npcs = _defeated_npcs(rows, opponents)
    bankruptcy_count = _event_count(conn, subject_type, subject_id, "bankruptcy")
    special_rat = _event_count(conn, subject_type, subject_id, "jungle_rat_captures_elephant")
    chip_events = [
        json.loads(row["data_json"] or "{}") for row in conn.execute(
            "SELECT data_json FROM achievement_events WHERE subject_type = ? AND subject_id = ? AND event_type = 'authoritative_chip_change'",
            (subject_type, subject_id),
        )
    ]
    max_loss = max((-row["chip_delta"] for row in rows if (row["chip_delta"] or 0) < 0), default=0)
    negative_balance = any(
        (row["chip_delta"] or 0) < 0 and row["balance_after"] is not None and row["balance_after"] < 0
        for row in rows
    ) or any(event.get("balance_after", 0) < 0 for event in chip_events)
    wallet = conn.execute(
        "SELECT balance, bankruptcy_count, bankruptcy_badge_active FROM chip_wallets WHERE subject_type = ? AND subject_id = ?",
        (subject_type, subject_id),
    ).fetchone()
    recovered = bool(
        wallet and wallet["bankruptcy_count"] > 0 and wallet["balance"] >= 200
        and not wallet["bankruptcy_badge_active"]
    )
    rematch_events = [
        dict(row) for row in conn.execute(
            "SELECT * FROM achievement_events WHERE subject_type = ? AND subject_id = ? AND event_type = 'rematch_created'",
            (subject_type, subject_id),
        )
    ]
    rematch_after_loss = False
    for event in rematch_events:
        data = json.loads(event["data_json"] or "{}")
        previous_id = data.get("rematch_of_room_id")
        previous = next((row for row in rows if row["room_id"] == previous_id), None)
        if previous and previous["outcome"] == "loss":
            rematch_after_loss = True
            break
    preserved_loss = _event_count(conn, subject_type, subject_id, "preserved_loss")
    loan_data = [
        (row["event_type"], json.loads(row["data_json"] or "{}"))
        for row in conn.execute(
            """
            SELECT event_type, data_json FROM achievement_events
            WHERE subject_type = ? AND subject_id = ? AND event_type LIKE 'loan_%'
            """,
            (subject_type, subject_id),
        )
    ]
    borrower_activations = sum(kind == "loan_activated_borrower" for kind, _ in loan_data)
    lender_activations = sum(kind == "loan_activated_lender" for kind, _ in loan_data)
    partial_repayments = sum(kind == "loan_partial_repayment" for kind, _ in loan_data)
    on_time_repayments = sum(
        kind == "loan_repaid" and bool(data.get("on_time"))
        for kind, data in loan_data
    )
    negative_borrower_loans = sum(
        kind == "loan_activated_lender"
        and isinstance(data.get("borrower_balance_before"), int)
        and data["borrower_balance_before"] < 0
        for kind, data in loan_data
    )
    maximum_active_debts = max(
        (
            int(data.get("active_debt_count", 0))
            for kind, data in loan_data if kind == "loan_activated_borrower"
        ),
        default=0,
    )
    became_debt_free = any(kind == "loan_debt_free" for kind, _ in loan_data)
    overdue_count = sum(kind == "loan_overdue" for kind, _ in loan_data)
    cap_reached_count = sum(kind == "loan_interest_cap_reached" for kind, _ in loan_data)
    created_count = _event_count(conn, subject_type, subject_id, "room_created") + len(rematch_events)
    zero_streak, best_zero_streak = _zero_stake_opponent_streak(rows, opponents)
    chain_count = _max_rematch_chain(rows, opponents)
    othello_tokens = {row["token"] for row in wins if row["game_type"] == "othello" and row["token"]}
    max_hours = max(
        (
            int((_parse_time(row["terminal_at"]) - _parse_time(row["created_at"])).total_seconds() // 3600)
            for row in rows
        ),
        default=0,
    )
    metrics: dict[str, tuple[int, bool, dict | None]] = {
        "first_normal_game": (total, total >= 1, None),
        "first_authoritative_rematch": (sum(bool(row["rematch_of_room_id"]) for row in rows), any(row["rematch_of_room_id"] for row in rows), None),
        "first_normal_draw": (len(draws), bool(draws), None),
        "six_game_types": (len(game_types), len(game_types) >= 6, {"game_types": sorted(game_types)}),
        "first_four_player_game": (sum(row["participant_count"] >= 4 for row in rows), any(row["participant_count"] >= 4 for row in rows), None),
        "first_staked_game": (sum(row["stake"] > 0 for row in rows), any(row["stake"] > 0 for row in rows), None),
        "win_after_three_losses": (loss_progress, comeback, None),
        "first_negative_balance": (int(negative_balance), negative_balance, None),
        "first_bankruptcy": (bankruptcy_count, bankruptcy_count >= 1, None),
        "bankruptcy_recovery": (int(recovered), recovered, None),
        "three_bankruptcies": (bankruptcy_count, bankruptcy_count >= 3, None),
        "lose_100_in_game": (max_loss, max_loss >= 100, None),
        "win_zero_stake": (sum(row["stake"] == 0 for row in wins), any(row["stake"] == 0 for row in wins), None),
        "lose_zero_stake": (sum(row["stake"] == 0 for row in losses), any(row["stake"] == 0 for row in losses), None),
        "ten_zero_stake_games": (sum(row["stake"] == 0 for row in rows), sum(row["stake"] == 0 for row in rows) >= 10, None),
        "five_zero_stake_same_opponent": (zero_streak, best_zero_streak >= 5, None),
        "jungle_rat_captures_elephant": (special_rat, special_rat >= 1, None),
        "all_in_loss": (sum(row["outcome"] == "loss" and row["opening_balance"] is not None and row["opening_balance"] > 0 and row["stake"] == row["opening_balance"] for row in rows), any(row["outcome"] == "loss" and row["opening_balance"] is not None and row["opening_balance"] > 0 and row["stake"] == row["opening_balance"] for row in rows), None),
        "settlement_balance_minus_500": (sum((row["chip_delta"] or 0) < 0 and row["balance_after"] is not None and row["balance_after"] <= -500 for row in rows), any((row["chip_delta"] or 0) < 0 and row["balance_after"] is not None and row["balance_after"] <= -500 for row in rows), None),
        "game_last_at_least_24h": (max_hours, max_hours >= 24, None),
        "ten_game_rematch_chain": (chain_count, chain_count >= 10, None),
        "non_tictactoe_draw": (sum(row["game_type"] != "tictactoe" for row in draws), any(row["game_type"] != "tictactoe" for row in draws), None),
        "othello_win_both_sides": (len(othello_tokens), len(othello_tokens) >= 2, {"tokens": sorted(othello_tokens)}),
        "last_move_comeback_win": (0, False, {"trigger": "not_implemented_without_authoritative_deficit"}),
        "loan_first_borrower_active": (borrower_activations, borrower_activations >= 1, None),
        "loan_first_lender_active": (lender_activations, lender_activations >= 1, None),
        "loan_first_partial_repayment": (partial_repayments, partial_repayments >= 1, None),
        "loan_first_ontime_repayment": (on_time_repayments, on_time_repayments >= 1, None),
        "loan_three_ontime_repayments": (on_time_repayments, on_time_repayments >= 3, None),
        "loan_lend_to_negative_borrower": (negative_borrower_loans, negative_borrower_loans >= 1, None),
        "loan_debt_free_after_three": (int(maximum_active_debts >= 3 and became_debt_free), maximum_active_debts >= 3 and became_debt_free, None),
        "loan_three_active": (maximum_active_debts, maximum_active_debts >= 3, None),
        "loan_first_overdue": (overdue_count, overdue_count >= 1, None),
        "loan_interest_cap_reached": (cap_reached_count, cap_reached_count >= 1, None),
    }
    for game_type, achievement_id in GAME_WIN_IDS.items():
        count = sum(row["game_type"] == game_type for row in wins)
        metrics[achievement_id] = (count, count >= 1, None)
    for persona_id, achievement_id in NPC_ACHIEVEMENT_IDS.items():
        metrics[achievement_id] = (int(persona_id in defeated_npcs), persona_id in defeated_npcs, None)
    metrics["defeat_all_six_npcs"] = (
        len(defeated_npcs & set(PRODUCTION_NPC_IDS)),
        set(PRODUCTION_NPC_IDS) <= defeated_npcs,
        {"persona_ids": sorted(defeated_npcs & set(PRODUCTION_NPC_IDS))},
    )

    if subject_type == "human":
        own_ai_loss = any(
            row["outcome"] == "loss" and any(
                opponent.get("participant_kind") == "bound_machine"
                and opponent["player_id"] == row.get("winner_player_id")
                for opponent in opponents.get(row["room_id"], [])
            )
            for row in rows
        )
        metrics.update({
            "human_rematch_after_loss": (int(rematch_after_loss), rematch_after_loss, None),
            "human_preserve_loss": (preserved_loss, preserved_loss >= 1, None),
            "human_loses_to_bound_ai": (int(own_ai_loss), own_ai_loss, None),
        })
    else:
        beats_human = any(
            row["outcome"] == "win" and any(opponent.get("participant_kind") == "human" for opponent in opponents.get(row["room_id"], []))
            for row in rows
        )
        revenge_progress, revenge = _ai_revenge_state(rows, opponents)
        win_streak, best_win_streak = _streak_state(rows, "win")
        metrics.update({
            "ai_creates_room": (created_count, created_count >= 1, None),
            "ai_beats_bound_human": (int(beats_human), beats_human, None),
            "ai_three_win_streak": (win_streak, best_win_streak >= 3, None),
            "ai_authoritative_rematch": (len(rematch_events), bool(rematch_events), None),
            "ai_revenge_bound_human": (revenge_progress, revenge, None),
            "ai_six_game_types": (len(game_types), len(game_types) >= 6, {"game_types": sorted(game_types)}),
        })

    unlocks: list[dict] = []
    for achievement_id, (current, qualified, detail) in metrics.items():
        definition = DEFINITIONS[achievement_id]
        if subject_type not in definition.subjects:
            continue
        _progress(conn, subject_type, subject_id, definition, current, detail=detail)
        if qualified:
            unlocked = _unlock(
                conn, subject_type, subject_id, achievement_id,
                source_type=source_type, source_id=source_id,
            )
            if unlocked:
                unlocks.append(unlocked)
    # Earlier unlocks in this same atomic evaluation can themselves carry the
    # bankrupt wallet across 200.  Recheck after every other automatic reward
    # so recovery is returned by the request that actually restored the balance.
    unlocks.extend(
        _refresh_bankruptcy_recovery(
            conn, subject_type, subject_id,
            source_type=source_type, source_id=source_id,
        )
    )
    return unlocks


def evaluate_relationship(
    conn: sqlite3.Connection,
    human_id: str,
    ai_id: str,
    *,
    source_type: str,
    source_id: str | None,
) -> list[dict]:
    if conn.execute(
        "SELECT 1 FROM achievement_relationships WHERE human_id = ? AND ai_id = ?",
        (human_id, ai_id),
    ).fetchone() is None:
        return []
    rows = [
        dict(row) for row in conn.execute(
            """
            SELECT match.*, human.outcome AS human_outcome, ai.outcome AS ai_outcome
            FROM achievement_matches AS match
            JOIN achievement_match_participants AS human ON human.room_id = match.room_id
            JOIN achievement_match_participants AS ai ON ai.room_id = match.room_id
            WHERE match.normal_outcome = 1
              AND human.subject_type = 'human' AND human.subject_id = ?
              AND ai.subject_type = 'ai' AND ai.subject_id = ?
            ORDER BY match.sequence
            """,
            (human_id, ai_id),
        )
    ]
    completed = len(rows)
    human_wins = sum(row["human_outcome"] == "win" and row["ai_outcome"] == "loss" for row in rows)
    ai_wins = sum(row["ai_outcome"] == "win" and row["human_outcome"] == "loss" for row in rows)
    decisive = human_wins + ai_wins
    dates = [_shanghai_date(row["terminal_at"]) for row in rows]
    full_days = max(
        ((later - earlier).days - 1 for earlier, later in zip(dates, dates[1:])),
        default=0,
    )
    same_day_checkin = conn.execute(
        """
        SELECT COUNT(*) FROM achievement_events AS human
        JOIN achievement_events AS ai ON ai.effective_date = human.effective_date
        WHERE human.event_type = 'check_in' AND ai.event_type = 'check_in'
          AND human.subject_type = 'human' AND human.subject_id = ?
          AND ai.subject_type = 'ai' AND ai.subject_id = ?
        """,
        (human_id, ai_id),
    ).fetchone()[0]
    loan_activations = [
        json.loads(row["data_json"] or "{}")
        for row in conn.execute(
            """
            SELECT data_json FROM achievement_events
            WHERE event_type = 'loan_activated_borrower'
              AND human_id = ? AND ai_id = ?
            """,
            (human_id, ai_id),
        )
    ]
    countered_loans = sum(
        int(item.get("counter_count", 0)) > 0 for item in loan_activations
    )
    loan_directions = {item.get("borrower_type") for item in loan_activations}
    metrics: dict[str, tuple[int, bool, dict | None]] = {
        "pair_first_game": (completed, completed >= 1, None),
        "pair_ten_games": (completed, completed >= 10, None),
        "pair_fifty_games": (completed, completed >= 50, None),
        "pair_reunion_after_seven_days": (full_days, full_days >= 7, {"complete_calendar_days": full_days}),
        "pair_same_day_check_in": (2 if same_day_checkin else 0, same_day_checkin > 0, None),
        "pair_both_won": (int(human_wins > 0) + int(ai_wins > 0), human_wins > 0 and ai_wins > 0, {"human_wins": human_wins, "ai_wins": ai_wins}),
        "pair_balanced_twenty": (decisive, decisive >= 20 and abs(human_wins - ai_wins) <= 2, {"human_wins": human_wins, "ai_wins": ai_wins}),
        "pair_five_wins_each": (min(human_wins, ai_wins), human_wins >= 5 and ai_wins >= 5, {"human_wins": human_wins, "ai_wins": ai_wins}),
        "loan_pair_counter_activated": (countered_loans, countered_loans >= 1, None),
        "loan_pair_bidirectional": (len(loan_directions & {"human", "ai"}), {"human", "ai"} <= loan_directions, {"borrower_types": sorted(loan_directions - {None})}),
    }
    context_key = _pair_key(human_id, ai_id)
    unlocks: list[dict] = []
    for achievement_id, (current, qualified, detail) in metrics.items():
        definition = DEFINITIONS[achievement_id]
        for subject_type, subject_id in (("human", human_id), ("ai", ai_id)):
            _progress(conn, subject_type, subject_id, definition, current, context_key=context_key, detail=detail)
            if qualified:
                unlocked = _unlock(
                    conn, subject_type, subject_id, achievement_id,
                    context_key=context_key, source_type=source_type, source_id=source_id,
                )
                if unlocked:
                    unlocks.append(unlocked)
    # Relationship rewards share the same wallet ledger and can be the exact
    # delta that clears a bankruptcy badge.  Resolve that derived achievement
    # before committing instead of waiting for an unrelated later event.
    for subject_type, subject_id in (("human", human_id), ("ai", ai_id)):
        unlocks.extend(
            _refresh_bankruptcy_recovery(
                conn, subject_type, subject_id,
                source_type=source_type, source_id=source_id,
            )
        )
    return unlocks


def _enabled_personas() -> set[str]:
    try:
        return {persona.id for persona in load_personas()}
    except PersonaConfigError:
        return set()


def _item_payload(
    definition: AchievementDefinition,
    progress: sqlite3.Row | None,
    unlock: sqlite3.Row | None,
) -> dict:
    current = progress["current_value"] if progress else 0
    target = progress["target_value"] if progress else definition.target
    if unlock is not None:
        current = max(current, target)
    payload = {
        "id": definition.id,
        "name": definition.name,
        "condition": definition.condition,
        "reward": definition.reward,
        "progress": {"current": min(current, target), "target": target},
        "unlocked": unlock is not None,
    }
    if unlock is not None:
        payload["unlocked_at"] = unlock["unlocked_at"]
    return payload


def get_achievements(
    subject_type: SubjectType,
    subject_id: str,
    *,
    bound_human_id: str | None = None,
) -> dict:
    """Project ordinary locked entries and unlocked hidden entries only."""
    from .database import connect

    context_key = (
        _pair_key(bound_human_id, subject_id)
        if subject_type == "ai" and bound_human_id else ""
    )
    conn = connect()
    try:
        progress_rows = {
            (row["achievement_id"], row["context_key"]): row
            for row in conn.execute(
                "SELECT * FROM achievement_progress WHERE subject_type = ? AND subject_id = ?",
                (subject_type, subject_id),
            )
        }
        unlock_rows = {
            (row["achievement_id"], row["context_key"]): row
            for row in conn.execute(
                "SELECT * FROM achievement_unlocks WHERE subject_type = ? AND subject_id = ?",
                (subject_type, subject_id),
            )
        }
    finally:
        conn.close()
    enabled_npcs = _enabled_personas()
    sections: list[dict] = []
    section_specs = [("common", "通用与棋种")]
    section_specs.append(("human", "人类专属") if subject_type == "human" else ("ai", "小机专属"))
    section_specs.append(("npc", "NPC 对手"))
    section_specs.append(("loan", "借款与欠条"))
    if context_key:
        section_specs.append(("relationship", "你们之间"))
    public_count = unlocked_public = 0
    for category, title in section_specs:
        items: list[dict] = []
        for definition in ACHIEVEMENT_CATALOG:
            if definition.hidden or definition.category != category or subject_type not in definition.subjects:
                continue
            item_context = context_key if category == "relationship" else ""
            unlock = unlock_rows.get((definition.id, item_context))
            if category == "npc":
                if definition.id == "defeat_all_six_npcs":
                    applicable = set(PRODUCTION_NPC_IDS) <= enabled_npcs
                else:
                    applicable = definition.npc_persona_id in enabled_npcs
                if not applicable and unlock is None:
                    continue
            public_count += 1
            unlocked_public += int(unlock is not None)
            items.append(_item_payload(definition, progress_rows.get((definition.id, item_context)), unlock))
        if items:
            sections.append({"id": category, "name": title, "items": items})

    hidden_items = []
    for definition in ACHIEVEMENT_CATALOG:
        if not definition.hidden or subject_type not in definition.subjects:
            continue
        unlock = unlock_rows.get((definition.id, ""))
        if unlock is None:
            continue
        hidden_items.append(_item_payload(definition, progress_rows.get((definition.id, "")), unlock))
    if hidden_items:
        sections.append({"id": "hidden", "name": "隐藏成就", "items": hidden_items})
    return {
        "subject_type": subject_type,
        "summary": {
            "unlocked": unlocked_public,
            "total": public_count,
            "hidden_unlocked": len(hidden_items),
        },
        "sections": sections,
    }


def compact_achievements(payload: dict) -> dict:
    """Keep MCP output bounded while retaining condition and reliable progress."""
    return {
        "summary": payload["summary"],
        "sections": [
            {
                "id": section["id"],
                "items": [
                    {
                        "id": item["id"], "name": item["name"],
                        "condition": item["condition"], "reward": item["reward"],
                        "progress": [item["progress"]["current"], item["progress"]["target"]],
                        "unlocked": item["unlocked"],
                        **({"at": item["unlocked_at"]} if item["unlocked"] else {}),
                    }
                    for item in section["items"]
                ],
            }
            for section in payload["sections"]
        ],
    }


def filter_unlocks(unlocks: Iterable[dict], subject_type: SubjectType, subject_id: str) -> list[dict]:
    return [
        {
            key: item[key]
            for key in ("id", "name", "reward", "unlocked_at", "context_key")
            if key in item
        }
        for item in unlocks
        if item.get("subject_type") == subject_type and item.get("subject_id") == subject_id
    ]


def backfill_authoritative_matches(conn: sqlite3.Connection) -> int:
    """Backfill only legacy terminal rows whose final revision proves move/resign."""
    from .database import decode_room

    candidates = conn.execute(
        """
        SELECT room.*,
               CASE
                 WHEN EXISTS (
                   SELECT 1 FROM room_messages AS event
                   WHERE event.room_id = room.room_id AND event.event_type = 'resign'
                     AND event.revision_at_send = room.revision
                 ) THEN 'resignation'
                 WHEN EXISTS (
                   SELECT 1 FROM room_messages AS event
                   WHERE event.room_id = room.room_id AND event.event_type = 'move'
                     AND event.revision_at_send = room.revision
                 ) THEN 'game_result'
                 ELSE NULL
               END AS proven_reason
        FROM rooms AS room
        WHERE room.status IN ('finished', 'archived')
          AND (room.winner IN ('human', 'ai', 'draw') OR room.winner_player_id IS NOT NULL)
          AND NOT EXISTS (
              SELECT 1 FROM achievement_matches AS saved WHERE saved.room_id = room.room_id
          )
        ORDER BY datetime(room.terminal_at), room.room_id
        """
    ).fetchall()
    count = 0
    for row in candidates:
        reason = row["proven_reason"]
        if reason is None:
            continue
        room = decode_room(row, conn)
        if len(room.get("participants", [])) > 2 and room.get("winner") != "draw":
            # Legacy role-only winner fields are ambiguous when several AI seats
            # exist.  Require the plugin result itself to name the exact winner.
            authoritative_winner = (room.get("result") or {}).get("winner_player_id")
            if not authoritative_winner or authoritative_winner != room.get("winner_player_id"):
                continue
        normal = reason == "game_result" or (
            reason == "resignation" and room.get("winner_player_id") is not None
        )
        if not normal:
            continue
        record_terminal_room(conn, room, reason, normal=True)
        _record_creation_fact(conn, room)
        if room.get("preserved"):
            human_losers = [
                participant["subject_id"]
                for participant in conn.execute(
                    """
                    SELECT subject_id FROM achievement_match_participants
                    WHERE room_id = ? AND subject_type = 'human'
                      AND outcome = 'loss'
                    """,
                    (room["room_id"],),
                )
            ]
            if len(human_losers) == 1:
                record_preserved_loss(conn, human_losers[0], room["room_id"])
        count += 1
    return count
