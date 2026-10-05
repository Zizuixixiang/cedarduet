"""宝石商人（gem_merchant）：两人宝石对决。

规则机制与 67 张发展卡的数值来自桌游《Splendor Duel》（Space Cowboys /
Asmodee，2022）的两人规则；游戏名、称号卡名称与全部美术均为原创。规则出处、
卡表来源与实现取舍见 docs/GEM_MERCHANT.md。

一回合拆成若干个服务端权威动作，全部经 ``legal_actions`` 发布：

- 可选阶段（phase=optional）：先用特权券（可多次）、再补充宝石盘（每回合一次），
  然后必须做一个必选动作（拿宝石 / 拿金保留 / 购买）；三样都做不了且已补盘或
  袋子为空时才允许 pass。
- 结算阶段（phase=resolve）：卡牌能力、称号卡与超过 10 枚的弃宝石。只有一个
  选项时服务端自动结算，多于一个才等玩家选择。

隐藏信息只有两类：三个牌堆的顺序（永不公开）与盲抽保留的卡（对局中对手只看到
等级，终局复盘公开）。从场上保留的卡对双方都公开。
"""
from __future__ import annotations

import random
from copy import deepcopy
from typing import Any

from .base import GamePlugin, MoveResult
from .tools import advance_flow, ensure_flow


GEM_COLORS = ("white", "blue", "green", "red", "black")
TOKEN_COLORS = GEM_COLORS + ("pearl", "gold")
COLOR_LABELS = {
    "white": "白", "blue": "蓝", "green": "绿", "red": "红", "black": "黑",
    "pearl": "珍珠", "gold": "金",
}
ABILITY_LABELS = {
    "extra_turn": "再来一回合",
    "take_gem": "从盘上拿 1 枚同色宝石",
    "steal": "从对手拿 1 枚宝石或珍珠",
    "privilege": "拿 1 张特权券",
}
LEVEL_LABELS = {1: "一级", 2: "二级", 3: "三级"}
PYRAMID_SIZES = {1: 5, 2: 4, 3: 3}
BOARD_SIZE = 5
WIN_POINTS = 20
WIN_CROWNS = 10
WIN_COLOR_POINTS = 10
TOKEN_LIMIT = 10
MAX_RESERVED = 3
PRIVILEGE_COUNT = 3
CROWN_THRESHOLDS = (3, 6)
HISTORY_LIMIT = 40
LINE_DIRECTIONS = ((0, 1), (1, 0), (1, 1), (1, -1))

_SHORT = {
    "w": "white", "u": "blue", "g": "green", "r": "red", "k": "black",
    "p": "pearl", "o": "gold",
}
_ABILITY = {
    "again": "extra_turn", "gem": "take_gem", "steal": "steal",
    "scroll": "privilege",
}

# 67 张发展卡：(等级, 颜色, 加成数, 分, 皇冠, 能力, 成本)。
# 颜色 "-" 为没有加成的纯分卡，"*" 为百搭卡。数值出处见 docs/GEM_MERCHANT.md：
# BGG plaidmac 卡表 v3 为主，CGY 数据交叉核对，唯一分歧一张以卡面图为准。
_RAW = (
    (1, "k", 1, 0, 0, "", "w1 u1 g1 r1"),
    (1, "k", 1, 0, 0, "again", "w2 u2 p1"),
    (1, "k", 1, 0, 0, "gem", "g2 r2"),
    (1, "k", 1, 1, 0, "", "u2 g3"),
    (1, "k", 1, 0, 1, "", "w3"),
    (1, "r", 1, 0, 0, "", "w1 u1 g1 k1"),
    (1, "r", 1, 0, 0, "again", "w2 k2 p1"),
    (1, "r", 1, 0, 0, "gem", "u2 g2"),
    (1, "r", 1, 1, 0, "", "w2 u3"),
    (1, "r", 1, 0, 1, "", "k3"),
    (1, "g", 1, 0, 0, "", "w1 u1 r1 k1"),
    (1, "g", 1, 0, 0, "again", "r2 k2 p1"),
    (1, "g", 1, 0, 0, "gem", "w2 u2"),
    (1, "g", 1, 1, 0, "", "w3 k2"),
    (1, "g", 1, 0, 1, "", "r3"),
    (1, "u", 1, 0, 0, "", "w1 g1 r1 k1"),
    (1, "u", 1, 0, 0, "again", "g2 r2 p1"),
    (1, "u", 1, 0, 0, "gem", "w2 k2"),
    (1, "u", 1, 1, 0, "", "r2 k3"),
    (1, "u", 1, 0, 1, "", "g3"),
    (1, "w", 1, 0, 0, "", "u1 g1 r1 k1"),
    (1, "w", 1, 0, 0, "again", "u2 g2 p1"),
    (1, "w", 1, 0, 0, "gem", "r2 k2"),
    (1, "w", 1, 1, 0, "", "g2 r3"),
    (1, "w", 1, 0, 1, "", "u3"),
    (1, "-", 0, 3, 0, "", "r4 p1"),
    (1, "*", 1, 1, 0, "joker", "k4 p1"),
    (1, "*", 1, 0, 1, "joker", "w4 p1"),
    (1, "*", 1, 1, 0, "joker", "w2 g2 k1 p1"),
    (1, "*", 1, 1, 0, "joker", "u2 r2 k1 p1"),
    (2, "k", 1, 1, 0, "steal", "w4 g3"),
    (2, "k", 2, 1, 0, "", "w5 u2"),
    (2, "k", 1, 2, 1, "", "u2 g2 r2 p1"),
    (2, "k", 1, 2, 0, "scroll", "r2 k4 p1"),
    (2, "r", 1, 1, 0, "steal", "u3 k4"),
    (2, "r", 2, 1, 0, "", "w2 k5"),
    (2, "r", 1, 2, 1, "", "w2 u2 g2 p1"),
    (2, "r", 1, 2, 0, "scroll", "g2 r4 p1"),
    (2, "g", 1, 1, 0, "steal", "w3 r4"),
    (2, "g", 2, 1, 0, "", "r5 k2"),
    (2, "g", 1, 2, 1, "", "w2 u2 k2 p1"),
    (2, "g", 1, 2, 0, "scroll", "u2 g4 p1"),
    (2, "u", 1, 1, 0, "steal", "g4 k3"),
    (2, "u", 2, 1, 0, "", "g5 r2"),
    (2, "u", 1, 2, 1, "", "w2 r2 k2 p1"),
    (2, "u", 1, 2, 0, "scroll", "w2 u4 p1"),
    (2, "w", 1, 1, 0, "steal", "u4 r3"),
    (2, "w", 2, 1, 0, "", "u5 g2"),
    (2, "w", 1, 2, 1, "", "g2 r2 k2 p1"),
    (2, "w", 1, 2, 0, "scroll", "w4 k2 p1"),
    (2, "-", 0, 5, 0, "", "u6 p1"),
    (2, "*", 1, 2, 0, "joker", "g6 p1"),
    (2, "*", 1, 0, 2, "joker", "g6 p1"),
    (2, "*", 1, 0, 2, "joker", "u6 p1"),
    (3, "k", 1, 3, 2, "", "w3 g5 r3 p1"),
    (3, "k", 1, 4, 0, "", "w2 r2 k6"),
    (3, "r", 1, 3, 2, "", "u5 g3 k3 p1"),
    (3, "r", 1, 4, 0, "", "g2 r6 k2"),
    (3, "g", 1, 3, 2, "", "w5 u3 r3 p1"),
    (3, "g", 1, 4, 0, "", "u2 g6 r2"),
    (3, "u", 1, 3, 2, "", "w3 g3 k5 p1"),
    (3, "u", 1, 4, 0, "", "w2 u6 g2"),
    (3, "w", 1, 3, 2, "", "u3 r5 k3 p1"),
    (3, "w", 1, 4, 0, "", "w6 u2 k2"),
    (3, "-", 0, 6, 0, "", "w8"),
    (3, "*", 1, 0, 3, "joker", "k8"),
    (3, "*", 1, 3, 0, "joker again", "r8"),
)


def _build_cards() -> dict[int, dict[str, Any]]:
    cards: dict[int, dict[str, Any]] = {}
    for card_id, (level, color, bonus, points, crowns, ability, cost) in enumerate(_RAW, 1):
        words = ability.split()
        raw_cost = {_SHORT[item[0]]: int(item[1:]) for item in cost.split()}
        cards[card_id] = {
            "id": card_id,
            "level": level,
            "color": _SHORT[color] if color in _SHORT else None,
            "joker": "joker" in words,
            "bonus": bonus,
            "points": points,
            "crowns": crowns,
            "ability": next((_ABILITY[w] for w in words if w != "joker"), None),
            "cost": {c: raw_cost[c] for c in TOKEN_COLORS if c in raw_cost},
        }
    return cards


CARDS = _build_cards()
assert len(CARDS) == 67
assert [sum(1 for c in CARDS.values() if c["level"] == lv) for lv in (1, 2, 3)] == [30, 24, 13]

# 称号卡（原版为四张皇室卡），名称原创，分值与能力照原版。
ROYALS = {
    1: {"id": 1, "name": "糖霜女王", "points": 3, "ability": None},
    2: {"id": 2, "name": "斗篷小偷", "points": 2, "ability": "steal"},
    3: {"id": 3, "name": "风车旅人", "points": 2, "ability": "extra_turn"},
    4: {"id": 4, "name": "卷轴管家", "points": 2, "ability": "privilege"},
}

# 宝石盘螺旋：从中心格起铺盘与补盘，[row, col] 零起始。
SPIRAL = (
    (2, 2), (3, 2), (3, 1), (2, 1), (1, 1), (1, 2), (1, 3), (2, 3), (3, 3),
    (4, 3), (4, 2), (4, 1), (4, 0), (3, 0), (2, 0), (1, 0), (0, 0), (0, 1),
    (0, 2), (0, 3), (0, 4), (1, 4), (2, 4), (3, 4), (4, 4),
)
assert sorted(SPIRAL) == [(r, c) for r in range(BOARD_SIZE) for c in range(BOARD_SIZE)]

INITIAL_TOKENS = (
    [color for color in GEM_COLORS for _ in range(4)] + ["pearl"] * 2 + ["gold"] * 3
)


def card_view(card_id: int) -> dict[str, Any]:
    return deepcopy(CARDS[card_id])


def _card_label(card_id: int) -> str:
    card = CARDS[card_id]
    if card["joker"]:
        kind = "百搭卡"
    elif card["color"]:
        kind = f"{COLOR_LABELS[card['color']]}卡"
    else:
        kind = "纯分卡"
    return f"{LEVEL_LABELS[card['level']]}{kind} #{card_id}"


def _tokens_text(tokens: dict[str, int]) -> str:
    parts = [f"{COLOR_LABELS[c]}{tokens[c]}" for c in TOKEN_COLORS if tokens.get(c)]
    return " ".join(parts) if parts else "0 枚"


def _cell_label(cell) -> str:
    return f"{'ABCDE'[cell[0]]}{cell[1] + 1}"


def _other(state: dict[str, Any], player_id: str) -> str:
    order = state["participant_order"]
    return order[1] if order[0] == player_id else order[0]


def card_color(item: dict[str, Any]) -> str | None:
    """A purchased joker counts as the color it was placed on."""
    return item.get("as") or CARDS[item["id"]]["color"]


def bonuses(state: dict[str, Any], player_id: str) -> dict[str, int]:
    totals = {c: 0 for c in GEM_COLORS}
    for item in state["players"][player_id]["cards"]:
        color = card_color(item)
        if color in totals:
            totals[color] += CARDS[item["id"]]["bonus"]
    return totals


def color_points(state: dict[str, Any], player_id: str) -> dict[str, int]:
    totals = {c: 0 for c in GEM_COLORS}
    for item in state["players"][player_id]["cards"]:
        color = card_color(item)
        if color in totals:
            totals[color] += CARDS[item["id"]]["points"]
    return totals


def prestige(state: dict[str, Any], player_id: str) -> int:
    player = state["players"][player_id]
    return (
        sum(CARDS[item["id"]]["points"] for item in player["cards"])
        + sum(ROYALS[rid]["points"] for rid in player["royals"])
    )


def crowns(state: dict[str, Any], player_id: str) -> int:
    return sum(CARDS[item["id"]]["crowns"] for item in state["players"][player_id]["cards"])


def token_total(state: dict[str, Any], player_id: str) -> int:
    return sum(state["players"][player_id]["tokens"].values())


def cost_after_bonus(state: dict[str, Any], player_id: str, card_id: int) -> dict[str, int]:
    owned = bonuses(state, player_id)
    due = {}
    for color, amount in CARDS[card_id]["cost"].items():
        need = max(0, amount - owned.get(color, 0))
        if need:
            due[color] = need
    return due


def gold_needed(state: dict[str, Any], player_id: str, card_id: int) -> int:
    tokens = state["players"][player_id]["tokens"]
    return sum(
        max(0, need - tokens.get(color, 0))
        for color, need in cost_after_bonus(state, player_id, card_id).items()
    )


def joker_colors(state: dict[str, Any], player_id: str) -> list[str]:
    return [color for color, count in bonuses(state, player_id).items() if count > 0]


def can_afford(state: dict[str, Any], player_id: str, card_id: int) -> bool:
    if CARDS[card_id]["joker"] and not joker_colors(state, player_id):
        return False
    return gold_needed(state, player_id, card_id) <= state["players"][player_id]["tokens"]["gold"]


def win_reason(state: dict[str, Any], player_id: str) -> str | None:
    points = prestige(state, player_id)
    if points >= WIN_POINTS:
        return f"声望 {points} 分"
    crown_count = crowns(state, player_id)
    if crown_count >= WIN_CROWNS:
        return f"{crown_count} 顶皇冠"
    per_color = color_points(state, player_id)
    best = max(GEM_COLORS, key=lambda c: per_color[c])
    if per_color[best] >= WIN_COLOR_POINTS:
        return f"{COLOR_LABELS[best]}色卡共 {per_color[best]} 分"
    return None


def _board_cells(state: dict[str, Any], predicate) -> list[tuple[int, int]]:
    board = state["board"]
    return [
        (r, c) for r in range(BOARD_SIZE) for c in range(BOARD_SIZE)
        if predicate(board[r][c])
    ]


def _takeable(value) -> bool:
    return value is not None and value != "gold"


def take_lines(state: dict[str, Any]) -> list[tuple[tuple[int, int], ...]]:
    """Every legal 1–3 token line, cells sorted by (row, col)."""
    board = state["board"]
    lines: set[tuple[tuple[int, int], ...]] = set()
    for r in range(BOARD_SIZE):
        for c in range(BOARD_SIZE):
            if not _takeable(board[r][c]):
                continue
            lines.add(((r, c),))
            for dr, dc in LINE_DIRECTIONS:
                cells = [(r, c)]
                for step in (1, 2):
                    rr, cc = r + dr * step, c + dc * step
                    if not (0 <= rr < BOARD_SIZE and 0 <= cc < BOARD_SIZE):
                        break
                    if not _takeable(board[rr][cc]):
                        break
                    cells.append((rr, cc))
                    lines.add(tuple(sorted(cells)))
    return sorted(lines, key=lambda line: (len(line), line))


def line_error(state: dict[str, Any], cells: list[tuple[int, int]]) -> str | None:
    """Human-readable reason a requested take is illegal, or ``None``."""
    if not 1 <= len(cells) <= 3:
        return "一次拿 1 到 3 枚宝石"
    if len(set(cells)) != len(cells):
        return "同一个格子不能选两次"
    board = state["board"]
    for r, c in cells:
        value = board[r][c]
        if value is None:
            return f"{_cell_label((r, c))} 是空格"
        if value == "gold":
            return "金不能直接拿；要拿金只能通过“拿金并保留一张卡”"
    if len(cells) == 1:
        return None
    ordered = sorted(cells)
    dr, dc = ordered[1][0] - ordered[0][0], ordered[1][1] - ordered[0][1]
    if (dr, dc) not in LINE_DIRECTIONS:
        return "这几枚不相邻：必须在同一条横、竖或斜线上紧挨着"
    for first, second in zip(ordered, ordered[1:]):
        if (second[0] - first[0], second[1] - first[1]) != (dr, dc):
            return "这几枚不在同一条直线上连续（中间不能隔空格或金）"
    return None


def _pyramid_ids(state: dict[str, Any]) -> list[int]:
    return [cid for lv in ("1", "2", "3") for cid in state["pyramid"][lv] if cid]


def _bag_count(state: dict[str, Any]) -> int:
    return sum(state["bag"].values())


def _int_value(value, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} 必须是整数")
    return value


def _cell_value(value, label: str = "cell") -> tuple[int, int]:
    if (
        not isinstance(value, (list, tuple)) or len(value) != 2
        or any(isinstance(v, bool) or not isinstance(v, int) for v in value)
    ):
        raise ValueError(f"{label} 必须是 [row, col] 两个整数")
    r, c = value
    if not (0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE):
        raise ValueError(f"{label} 越界：row 和 col 都在 0–4 之间")
    return int(r), int(c)


def _color_value(value, allowed, label: str) -> str:
    if value not in allowed:
        names = "/".join(allowed)
        raise ValueError(f"{label} 只能是 {names}")
    return value


COLOR_CODES = {"white": "W", "blue": "U", "green": "G", "red": "R", "black": "K",
               "pearl": "P", "gold": "O"}


def tokens_code(amounts: dict[str, int]) -> str:
    return " ".join(f"{COLOR_CODES[c]}{amounts[c]}" for c in TOKEN_COLORS if amounts.get(c))


def card_code(card: dict[str, Any] | None) -> str | None:
    """Compact, LLM-readable card text used only by MCP projections."""
    if card is None:
        return None
    if card.get("hidden"):
        return f"hidden L{card['level']}"
    if card.get("as"):
        kind = f"joker-as-{card['as']}+{card['bonus']}"
    elif card["joker"]:
        kind = f"joker+{card['bonus']}"
    elif card["color"]:
        kind = f"{card['color']}+{card['bonus']}"
    else:
        kind = "points-only"
    parts = [f"#{card['id']}", f"L{card['level']}", kind]
    if card["points"]:
        parts.append(f"{card['points']}pt")
    if card["crowns"]:
        parts.append(f"{card['crowns']}crown")
    if card["ability"]:
        parts.append(card["ability"])
    parts.append("=" + tokens_code(card["cost"]))
    if card.get("blind"):
        parts.append("blind")
    return " ".join(parts)


def compact_public(public: dict[str, Any]) -> dict[str, Any]:
    """MCP view of the already-safe public projection (never raw state)."""
    players = {}
    for pid, p in public["players"].items():
        players[pid] = {
            "tokens": tokens_code(p["tokens"]),
            "privileges": p["privileges"],
            "points": p["points"],
            "crowns": p["crowns"],
            "bonuses": tokens_code(p["bonuses"]),
            "color_points": tokens_code(p["color_points"]),
            "purchased": [item["id"] for item in p["purchased"]],
            "reserved": [card_code(card) for card in p["reserved"]],
            "royals": [r["id"] for r in p["royals"]],
        }
    return {
        "board": ["".join(COLOR_CODES[t] if t else "." for t in row) for row in public["board"]],
        "bag": tokens_code(public["bag"]),
        "privileges_on_table": public["privileges_on_table"],
        "pyramid": {lv: [card_code(card) for card in row] for lv, row in public["pyramid"].items()},
        "deck_counts": dict(public["deck_counts"]),
        "royals_available": [r["id"] for r in public["royals_available"]],
        "players": players,
        "pending": deepcopy(public["pending"]),
        "extra_turn": public["extra_turn"],
        "refilled": public["refilled"],
        "winner_player_id": public["winner_player_id"],
        "finish_reason": public["finish_reason"],
    }


ACTION_KEYS = {
    "use_privilege": ({"action", "cell"}, set()),
    "refill": ({"action"}, set()),
    "take": ({"action", "cells"}, set()),
    "reserve": ({"action"}, {"gold", "card_id", "level"}),
    "buy": ({"action", "card_id"}, {"joker_color"}),
    "pass": ({"action"}, set()),
    "take_bonus_gem": ({"action", "cell"}, set()),
    "steal": ({"action", "gem"}, set()),
    "choose_royal": ({"action", "royal_id"}, set()),
    "discard": ({"action", "gem"}, set()),
}
OPTIONAL_ACTIONS = {"use_privilege", "refill"}
MANDATORY_ACTIONS = {"take", "reserve", "buy", "pass"}
PENDING_ACTIONS = {
    "take_gem": "take_bonus_gem",
    "steal": "steal",
    "royal": "choose_royal",
    "discard": "discard",
}


class GemMerchant(GamePlugin):
    game_type = "gem_merchant"
    display_name = "宝石商人"
    category = "tabletop"
    min_players = 2
    max_players = 2
    allowed_player_counts = (2,)
    recommended_players = 2
    supports_npcs = False
    supports_stakes = True
    # Refills, newly revealed pyramid cards and auto-resolved abilities are
    # random or rule-derived; the mover needs the public delta immediately.
    mcp_immediate_public_events = True
    rules_text = (
        "【目标】\n"
        "- 两人对决。回合结束时满足任一条立刻获胜：声望达到 20 分；皇冠达到 10 顶；"
        "同一种颜色的卡累计 10 分（百搭卡算它压着的颜色，纯分卡不算任何颜色）。\n"
        "- 配件：5×5 宝石盘，白/蓝/绿/红/黑各 4 枚、珍珠 2 枚、金 3 枚；3 张特权券；"
        "一级 30 张、二级 24 张、三级 13 张发展卡；4 张称号卡。\n"
        "- 开局翻出一级 5 张、二级 4 张、三级 3 张；宝石随机从中心沿螺旋铺满盘面；"
        "后手先拿 1 张特权券。\n\n"
        "【行动】\n"
        "- 每回合先做 0–2 个可选行动，顺序固定：先用特权券，再补盘；然后必须做 1 个必选行动。\n"
        "- 用特权券：每交回 1 张，从盘上拿任意 1 枚非金宝石（珍珠可以）。补过盘后本回合不能再用。\n"
        "- 补盘：袋中宝石全部随机从中心沿螺旋填回空格；每回合最多一次，补完对手拿 1 张特权券。\n"
        "- 必选一：拿 1–3 枚在同一条横、竖、斜线上紧挨着的非金宝石，中间不能隔空格或金。"
        "一次拿 3 枚同色、或拿到 2 枚珍珠时，对手拿 1 张特权券。\n"
        "- 必选二：保留区不足 3 张且盘上有金时，拿 1 枚金，并保留场上任意 1 张卡或从任一牌堆顶盲抽 1 张。\n"
        "- 必选三：购买场上或自己保留区的 1 张卡。成本先扣同色加成（每色最多扣到 0，珍珠没有加成），"
        "剩下的付宝石，金可以顶任何颜色或珍珠；付出的宝石回到袋中。\n"
        "- 三样必选都做不了时，必须先补盘；补盘后仍做不了（或袋子为空）才能 pass。\n\n"
        "【特殊规则】\n"
        "- 拿特权券：桌上有就从桌上拿，桌上没有就从对手那里拿，3 张都在自己手里则不变。\n"
        "- 卡牌能力在购买后立即结算：再来一回合；从盘上拿 1 枚同色宝石；从对手拿 1 枚非金宝石或珍珠；"
        "拿 1 张特权券；百搭卡必须压在自己已有加成的一种颜色上，没有加成卡时不能买。\n"
        "- 皇冠累计到第 3 顶、第 6 顶时各选 1 张称号卡：糖霜女王 3 分；斗篷小偷 2 分并从对手拿 1 枚；"
        "风车旅人 2 分并再来一回合；卷轴管家 2 分并拿 1 张特权券。\n"
        "- 场上空位从同级牌堆补，牌堆空了就空着。从场上保留的卡双方可见；盲抽的卡对手只看到等级，终局公开。\n"
        "- 回合结束时宝石（含珍珠与金）超过 10 枚，要逐枚弃回袋中直到 10 枚。\n"
        "- 只有一个选项的能力、称号卡与弃宝石由服务端自动结算。\n\n"
        "【胜负】\n"
        "- 回合结束（弃宝石之后）检查三条胜利条件，达成即胜；再来一回合在胜负检查之后才生效。\n"
        "- 极端情况下双方连续都只能 pass，按声望分高低判胜，相同为平局。\n"
        "- 本作规则机制与卡牌数值来自《Splendor Duel》，名称与美术为原创。\n\n"
        "制作：顾屿、相顾｜小红书：苏苏脆脆"
    )
    move_format = (
        '坐标 cell 为零起始 [row,col]，row/col 都在 0–4；宝石颜色为 '
        'white/blue/green/red/black/pearl/gold。'
        '可选：{"move":{"action":"use_privilege","cell":[2,3]}}；{"move":{"action":"refill"}}。'
        '必选：拿宝石 {"move":{"action":"take","cells":[[0,0],[0,1],[0,2]]}}；'
        '保留 {"move":{"action":"reserve","gold":[4,4],"card_id":12}} 或盲抽 '
        '{"move":{"action":"reserve","gold":[4,4],"level":2}}（gold 省略时取第一枚金）；'
        '购买 {"move":{"action":"buy","card_id":40}}，百搭卡另传 "joker_color":"red"；'
        '无事可做时 {"move":{"action":"pass"}}。'
        '结算：{"move":{"action":"take_bonus_gem","cell":[1,1]}}、'
        '{"move":{"action":"steal","gem":"pearl"}}、'
        '{"move":{"action":"choose_royal","royal_id":3}}、'
        '{"move":{"action":"discard","gem":"red"}}（每次弃 1 枚）。'
        '轮到你时 private_state 给出 legal_actions（可选阶段为 legal_summary 摘要）；'
        '对局中每个动作后 gem_merchant_delta 给出变化的公开字段。'
    )
    mcp_move_format = move_format + (
        '普通轮次 legal_summary.take 为合法取法数，use_privilege=true 表示可用券；'
        '取宝石仍按上述坐标格式及相邻直线规则提交，空格和金不能跨过。'
        'legal_summary 每次整体替换，未列出的动作不可用。reserve 的 gold/card_ids/levels 分别列出'
        '可选金格/场上卡号/盲抽等级；buy 中数字即 card_id，对象则列出 card_id 与可选 joker_color。'
        '普通轮次 private_state.blind_reserved 是自己当前全部盲抽保留卡（空数组表示没有），'
        '场上保留卡从公开 players 中读取；bootstrap/full_state 的 reserved 仍给全部保留卡。'
    )

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.SystemRandom()

    # ------------------------------------------------------------------
    # setup

    @staticmethod
    def _fixture() -> list[dict[str, Any]]:
        return [
            {"player_id": f"player-{index + 1}", "seat_index": index,
             "role": "human" if index == 0 else "ai"}
            for index in range(2)
        ]

    def initial_state(self) -> dict[str, Any]:
        return self.initialize(self._fixture())

    def initialize(self, participants: list[dict[str, Any]]) -> dict[str, Any]:
        order = [
            str(item["player_id"])
            for item in sorted(participants, key=lambda item: item.get("seat_index", 0))
        ]
        if len(order) != 2 or len(set(order)) != 2:
            raise ValueError("宝石商人只支持 2 名不同玩家")
        opener = next(
            (str(item["player_id"]) for item in participants if item.get("_opening_player")),
            order[0],
        )
        tokens = list(INITIAL_TOKENS)
        self._rng.shuffle(tokens)
        board: list[list[str | None]] = [[None] * BOARD_SIZE for _ in range(BOARD_SIZE)]
        for (r, c), token in zip(SPIRAL, tokens):
            board[r][c] = token
        decks: dict[str, list[int]] = {}
        pyramid: dict[str, list[int | None]] = {}
        for level in (1, 2, 3):
            ids = [cid for cid, card in CARDS.items() if card["level"] == level]
            self._rng.shuffle(ids)
            size = PYRAMID_SIZES[level]
            pyramid[str(level)] = ids[:size]
            decks[str(level)] = ids[size:]
        state: dict[str, Any] = {
            "board_kind": self.game_type,
            "participant_order": order,
            "first_player_id": opener,
            "turn_player_id": opener,
            "board": board,
            "bag": {color: 0 for color in TOKEN_COLORS},
            "privileges_on_table": PRIVILEGE_COUNT,
            "decks": decks,
            "pyramid": pyramid,
            "royals": sorted(ROYALS),
            "players": {
                pid: {
                    "tokens": {color: 0 for color in TOKEN_COLORS},
                    "cards": [],
                    "reserved": [],
                    "royals": [],
                    "privileges": 0,
                }
                for pid in order
            },
            "turn_state": {"refilled": False, "pending": [], "extra_turn": False},
            "consecutive_passes": 0,
            "winner_player_id": None,
            "result": None,
            "finish_reason": None,
            "last_action": None,
            "action_history": [],
        }
        ensure_flow(state, phase="optional")
        self._set_opener(state, opener)
        return state

    @staticmethod
    def _set_opener(state: dict[str, Any], opener: str) -> None:
        state["first_player_id"] = opener
        state["turn_player_id"] = opener
        for pid in state["participant_order"]:
            state["players"][pid]["privileges"] = 0
        state["players"][_other(state, opener)]["privileges"] = 1
        state["privileges_on_table"] = PRIVILEGE_COUNT - 1

    def prepare_opening_state(
        self,
        state: dict[str, Any],
        first_player_id: str,
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del participants
        if first_player_id in state["participant_order"]:
            self._set_opener(state, first_player_id)
        return state

    # ------------------------------------------------------------------
    # legality

    @staticmethod
    def _is_finished(state: dict[str, Any]) -> bool:
        return bool(state.get("result")) or state.get("flow", {}).get("phase") == "finished"

    @staticmethod
    def _can_reserve(state: dict[str, Any], player_id: str) -> bool:
        if len(state["players"][player_id]["reserved"]) >= MAX_RESERVED:
            return False
        if not _board_cells(state, lambda v: v == "gold"):
            return False
        return bool(_pyramid_ids(state)) or any(state["decks"][lv] for lv in ("1", "2", "3"))

    def _buy_actions(self, state: dict[str, Any], player_id: str) -> list[dict[str, Any]]:
        actions = []
        own = [item["id"] for item in state["players"][player_id]["reserved"]]
        for card_id in _pyramid_ids(state) + own:
            if not can_afford(state, player_id, card_id):
                continue
            if CARDS[card_id]["joker"]:
                for color in joker_colors(state, player_id):
                    actions.append({"action": "buy", "card_id": card_id, "joker_color": color})
            else:
                actions.append({"action": "buy", "card_id": card_id})
        return actions

    def _reserve_actions(self, state: dict[str, Any], player_id: str) -> list[dict[str, Any]]:
        if not self._can_reserve(state, player_id):
            return []
        actions = []
        targets: list[dict[str, Any]] = [{"card_id": cid} for cid in _pyramid_ids(state)]
        targets += [{"level": int(lv)} for lv in ("1", "2", "3") if state["decks"][lv]]
        for r, c in _board_cells(state, lambda v: v == "gold"):
            for target in targets:
                actions.append({"action": "reserve", "gold": [r, c], **target})
        return actions

    def _mandatory_actions(self, state: dict[str, Any], player_id: str) -> list[dict[str, Any]]:
        actions = [
            {"action": "take", "cells": [list(cell) for cell in line]}
            for line in take_lines(state)
        ]
        actions += self._reserve_actions(state, player_id)
        actions += self._buy_actions(state, player_id)
        return actions

    def _pending_options(
        self, state: dict[str, Any], player_id: str, item: dict[str, Any]
    ) -> list[Any]:
        kind = item["kind"]
        if kind == "take_gem":
            return [list(cell) for cell in _board_cells(state, lambda v: v == item["color"])]
        if kind == "steal":
            tokens = state["players"][_other(state, player_id)]["tokens"]
            return [c for c in TOKEN_COLORS if c != "gold" and tokens[c] > 0]
        if kind == "royal":
            return list(state["royals"])
        if kind == "discard":
            tokens = state["players"][player_id]["tokens"]
            return [c for c in TOKEN_COLORS if tokens[c] > 0]
        return []

    def _legal_actions_for(self, state: dict[str, Any], player_id: str) -> list[dict[str, Any]]:
        if self._is_finished(state) or state.get("turn_player_id") != player_id:
            return []
        phase = state["flow"]["phase"]
        turn = state["turn_state"]
        if phase == "resolve":
            if not turn["pending"]:
                return []
            item = turn["pending"][0]
            action = PENDING_ACTIONS[item["kind"]]
            key = {"take_bonus_gem": "cell", "steal": "gem",
                   "choose_royal": "royal_id", "discard": "gem"}[action]
            return [
                {"action": action, key: option}
                for option in self._pending_options(state, player_id, item)
            ]
        actions: list[dict[str, Any]] = []
        me = state["players"][player_id]
        if not turn["refilled"]:
            if me["privileges"] > 0:
                actions += [
                    {"action": "use_privilege", "cell": [r, c]}
                    for r, c in _board_cells(state, _takeable)
                ]
            if _bag_count(state) > 0:
                actions.append({"action": "refill"})
        mandatory = self._mandatory_actions(state, player_id)
        actions += mandatory
        if not mandatory and (turn["refilled"] or _bag_count(state) == 0):
            actions.append({"action": "pass"})
        return actions

    def _normalize(
        self, state: dict[str, Any], move: dict[str, Any], player_id: str
    ) -> dict[str, Any]:
        """Validate with readable errors and return the canonical legal move."""
        if self._is_finished(state):
            raise ValueError("对局已经结束")
        if state.get("turn_player_id") != player_id:
            raise ValueError("还没轮到你")
        if not isinstance(move, dict):
            raise ValueError("move 必须是对象")
        action = move.get("action")
        if action not in ACTION_KEYS:
            raise ValueError(f"未知动作：{action}")
        required, optional = ACTION_KEYS[action]
        keys = set(move)
        if not required <= keys or keys - required - optional:
            raise ValueError(f"{action} 的参数不正确，请对照 move_format")
        phase = state["flow"]["phase"]
        turn = state["turn_state"]
        me = state["players"][player_id]

        if phase == "resolve":
            item = turn["pending"][0] if turn["pending"] else None
            expected = PENDING_ACTIONS.get(item["kind"]) if item else None
            if action != expected:
                raise ValueError(f"现在需要先完成结算：{self._pending_text(state, player_id)}")
        elif action in PENDING_ACTIONS.values():
            raise ValueError("现在没有需要结算的能力或弃宝石")

        if action == "use_privilege":
            if turn["refilled"]:
                raise ValueError("补过盘后本回合不能再用特权券")
            if me["privileges"] <= 0:
                raise ValueError("你没有特权券")
            r, c = _cell_value(move["cell"])
            value = state["board"][r][c]
            if value is None:
                raise ValueError(f"{_cell_label((r, c))} 是空格")
            if value == "gold":
                raise ValueError("特权券不能换金")
            canonical = {"action": action, "cell": [r, c]}
        elif action == "refill":
            if turn["refilled"]:
                raise ValueError("本回合已经补过盘")
            if _bag_count(state) == 0:
                raise ValueError("袋子是空的，不能补盘")
            canonical = {"action": action}
        elif action == "take":
            cells = move["cells"]
            if not isinstance(cells, list):
                raise ValueError("cells 必须是 [row,col] 数组")
            parsed = [_cell_value(cell, "cells 的每一项") for cell in cells]
            error = line_error(state, parsed)
            if error:
                raise ValueError(error)
            canonical = {"action": action, "cells": [list(cell) for cell in sorted(parsed)]}
        elif action == "reserve":
            has_card = "card_id" in move
            has_level = "level" in move
            if has_card == has_level:
                raise ValueError("保留时 card_id（场上的卡）和 level（盲抽牌堆）二选一")
            if len(me["reserved"]) >= MAX_RESERVED:
                raise ValueError("保留区已满（最多 3 张）")
            golds = _board_cells(state, lambda v: v == "gold")
            if not golds:
                raise ValueError("盘上没有金，现在不能保留")
            gold = _cell_value(move["gold"], "gold") if "gold" in move else golds[0]
            if gold not in golds:
                raise ValueError(f"{_cell_label(gold)} 上不是金")
            if has_card:
                card_id = _int_value(move["card_id"], "card_id")
                if card_id not in _pyramid_ids(state):
                    raise ValueError(f"#{card_id} 不在场上；场上的卡才能直接保留，否则用 level 盲抽")
                canonical = {"action": action, "gold": list(gold), "card_id": card_id}
            else:
                level = _int_value(move["level"], "level")
                if level not in (1, 2, 3):
                    raise ValueError("level 只能是 1、2、3")
                if not state["decks"][str(level)]:
                    raise ValueError(f"{LEVEL_LABELS[level]}牌堆已经空了")
                canonical = {"action": action, "gold": list(gold), "level": level}
        elif action == "buy":
            card_id = _int_value(move["card_id"], "card_id")
            own = [item["id"] for item in me["reserved"]]
            if card_id not in _pyramid_ids(state) and card_id not in own:
                raise ValueError(f"#{card_id} 不在场上，也不在你的保留区")
            card = CARDS[card_id]
            canonical = {"action": action, "card_id": card_id}
            if card["joker"]:
                options = joker_colors(state, player_id)
                if not options:
                    raise ValueError("百搭卡要压在一张有加成的卡上；你还没有带加成的卡")
                color = move.get("joker_color")
                if color is None:
                    if len(options) != 1:
                        raise ValueError(
                            "百搭卡要指定 joker_color：" + "/".join(options)
                        )
                    color = options[0]
                color = _color_value(color, options, "joker_color")
                canonical["joker_color"] = color
            elif "joker_color" in move:
                raise ValueError("只有百搭卡需要 joker_color")
            short = gold_needed(state, player_id, card_id)
            if short > me["tokens"]["gold"]:
                raise ValueError(
                    f"买不起 #{card_id}：还要付 {_tokens_text(cost_after_bonus(state, player_id, card_id))}，"
                    f"差 {short} 枚而金只有 {me['tokens']['gold']} 枚"
                )
        elif action == "pass":
            if self._mandatory_actions(state, player_id):
                raise ValueError("还有能做的必选动作，不能 pass")
            if not turn["refilled"] and _bag_count(state) > 0:
                raise ValueError("三样必选都做不了时要先补盘")
            canonical = {"action": action}
        elif action == "take_bonus_gem":
            canonical = {"action": action, "cell": list(_cell_value(move["cell"]))}
        elif action == "steal":
            canonical = {"action": action, "gem": _color_value(
                move["gem"], tuple(c for c in TOKEN_COLORS if c != "gold"), "gem")}
        elif action == "choose_royal":
            canonical = {"action": action, "royal_id": _int_value(move["royal_id"], "royal_id")}
        else:  # discard
            canonical = {"action": action, "gem": _color_value(move["gem"], TOKEN_COLORS, "gem")}

        if action in MANDATORY_ACTIONS | OPTIONAL_ACTIONS and phase != "optional":
            raise ValueError("本回合的必选动作已经做过了")
        if canonical not in self._legal_actions_for(state, player_id):
            if action in PENDING_ACTIONS.values():
                raise ValueError(f"这个选择不可用：{self._pending_text(state, player_id)}")
            raise ValueError("该动作不在服务端权威 legal_actions 中")
        return canonical

    def validate_action(
        self, state: dict[str, Any], move: dict[str, Any], actor: dict[str, Any]
    ) -> None:
        self._normalize(state, move, str(actor["player_id"]))

    def validate_move(self, state, move, mark):
        del state, move, mark
        raise ValueError("宝石商人使用带玩家身份的 action 接口")

    def apply_move(self, state, move, mark):
        del state, move, mark
        raise ValueError("宝石商人使用带玩家身份的 action 接口")

    def check_winner(self, state):
        del state
        return None

    # ------------------------------------------------------------------
    # rule helpers that mutate state

    @staticmethod
    def _gain_privilege(state: dict[str, Any], player_id: str) -> str:
        me = state["players"][player_id]
        opponent = state["players"][_other(state, player_id)]
        if state["privileges_on_table"] > 0:
            state["privileges_on_table"] -= 1
            me["privileges"] += 1
            return "从桌上拿 1 张特权券"
        if opponent["privileges"] > 0:
            opponent["privileges"] -= 1
            me["privileges"] += 1
            return "桌上没有特权券，从对手那里拿走 1 张"
        return "3 张特权券都已在手里，不再增加"

    @staticmethod
    def _replace_pyramid_slot(state: dict[str, Any], card_id: int) -> tuple[str, int, int | None]:
        for level in ("1", "2", "3"):
            row = state["pyramid"][level]
            if card_id in row:
                index = row.index(card_id)
                deck = state["decks"][level]
                row[index] = deck.pop(0) if deck else None
                return level, index, row[index]
        raise ValueError(f"#{card_id} 不在场上")

    def _pending_text(self, state: dict[str, Any], player_id: str) -> str:
        pending = state["turn_state"]["pending"]
        if not pending:
            return "无"
        item = pending[0]
        if item["kind"] == "take_gem":
            return f"从盘上拿 1 枚{COLOR_LABELS[item['color']]}"
        if item["kind"] == "steal":
            return "从对手那里拿 1 枚非金宝石或珍珠"
        if item["kind"] == "royal":
            return "选 1 张称号卡"
        need = token_total(state, player_id) - TOKEN_LIMIT
        return f"宝石超过 10 枚，还要弃 {max(need, 0)} 枚"

    def _resolve_pending(
        self, state: dict[str, Any], player_id: str, item: dict[str, Any],
        choice: Any, notes: list[str], effects: list[dict[str, Any]],
    ) -> None:
        kind = item["kind"]
        pending = state["turn_state"]["pending"]
        me = state["players"][player_id]
        if kind == "take_gem":
            pending.remove(item)
            r, c = choice
            color = state["board"][r][c]
            state["board"][r][c] = None
            me["tokens"][color] += 1
            notes.append(f"卡牌能力：从 {_cell_label((r, c))} 拿 1 枚{COLOR_LABELS[color]}")
            effects.append({"effect": "take_gem", "cell": [r, c], "gem": color})
        elif kind == "steal":
            pending.remove(item)
            opponent = state["players"][_other(state, player_id)]
            opponent["tokens"][choice] -= 1
            me["tokens"][choice] += 1
            notes.append(f"从对手那里拿走 1 枚{COLOR_LABELS[choice]}")
            effects.append({"effect": "steal", "gem": choice})
        elif kind == "royal":
            pending.remove(item)
            state["royals"].remove(choice)
            me["royals"].append(choice)
            royal = ROYALS[choice]
            notes.append(f"请来称号卡「{royal['name']}」（{royal['points']} 分）")
            effects.append({"effect": "royal", "royal_id": choice})
            if royal["ability"] == "steal":
                pending.insert(0, {"kind": "steal"})
            elif royal["ability"] == "extra_turn":
                state["turn_state"]["extra_turn"] = True
            elif royal["ability"] == "privilege":
                notes.append("称号卡能力：" + self._gain_privilege(state, player_id))
        elif kind == "discard":
            me["tokens"][choice] -= 1
            state["bag"][choice] += 1
            notes.append(f"弃回 1 枚{COLOR_LABELS[choice]}")
            effects.append({"effect": "discard", "gem": choice})
            if token_total(state, player_id) <= TOKEN_LIMIT:
                pending.remove(item)

    def _advance(
        self, state: dict[str, Any], player_id: str,
        notes: list[str], effects: list[dict[str, Any]],
    ) -> bool:
        """Auto-resolve forced choices. Return True once the turn is complete."""
        pending = state["turn_state"]["pending"]
        while True:
            if pending:
                item = pending[0]
                if item["kind"] == "discard" and token_total(state, player_id) <= TOKEN_LIMIT:
                    pending.pop(0)
                    continue
                options = self._pending_options(state, player_id, item)
                if not options:
                    pending.pop(0)
                    notes.append({
                        "take_gem": "盘上没有这种颜色，“拿同色”跳过",
                        "steal": "对手没有能拿的宝石，跳过",
                        "royal": "称号卡已经全部被选走",
                        "discard": "",
                    }[item["kind"]])
                    continue
                if len(options) == 1:
                    self._resolve_pending(state, player_id, item, options[0], notes, effects)
                    continue
                state["flow"]["phase"] = "resolve"
                return False
            if token_total(state, player_id) > TOKEN_LIMIT:
                pending.append({"kind": "discard"})
                continue
            return True

    def _finish(self, state: dict[str, Any], winner: str | None, reason: str) -> dict[str, Any]:
        state["flow"]["phase"] = "finished"
        state["turn_state"] = {"refilled": False, "pending": [], "extra_turn": False}
        state["winner_player_id"] = winner
        state["finish_reason"] = reason
        state["turn_player_id"] = None
        if winner:
            result = {"winner_player_id": winner, "draw": False, "reason": reason}
        else:
            result = {"draw": True, "reason": reason}
        state["result"] = result
        return result

    def _end_turn(self, state: dict[str, Any], player_id: str, notes: list[str]) -> MoveResult:
        reason = win_reason(state, player_id)
        if reason:
            result = self._finish(state, player_id, reason)
            notes.append(f"达成胜利条件：{reason}")
            return MoveResult(state=state, note="；".join(n for n in notes if n), result=deepcopy(result))
        extra = state["turn_state"]["extra_turn"]
        state["turn_state"] = {"refilled": False, "pending": [], "extra_turn": False}
        state["flow"]["phase"] = "optional"
        advance_flow(state)
        if extra:
            notes.append("再来一回合")
            return MoveResult(state=state, retain_turn=True, note="；".join(n for n in notes if n))
        nxt = _other(state, player_id)
        state["turn_player_id"] = nxt
        return MoveResult(state=state, next_player_id=nxt, note="；".join(n for n in notes if n))

    # ------------------------------------------------------------------
    # apply

    def apply_action(
        self, state: dict[str, Any], move: dict[str, Any], actor: dict[str, Any]
    ) -> MoveResult:
        player_id = str(actor["player_id"])
        move = self._normalize(state, move, player_id)
        before = self._delta_basis(state)
        action = move["action"]
        me = state["players"][player_id]
        turn = state["turn_state"]
        notes: list[str] = []
        effects: list[dict[str, Any]] = []
        record: dict[str, Any] = {"player_id": player_id, "action": action}
        complete = False

        if action == "use_privilege":
            r, c = move["cell"]
            color = state["board"][r][c]
            state["board"][r][c] = None
            me["tokens"][color] += 1
            me["privileges"] -= 1
            state["privileges_on_table"] += 1
            record.update(cell=[r, c], gem=color)
            notes.append(f"用 1 张特权券拿 {_cell_label((r, c))} 的{COLOR_LABELS[color]}")
        elif action == "refill":
            pool = [c for c in TOKEN_COLORS for _ in range(state["bag"][c])]
            self._rng.shuffle(pool)
            placed = 0
            for r, c in SPIRAL:
                if not pool:
                    break
                if state["board"][r][c] is None:
                    state["board"][r][c] = pool.pop()
                    placed += 1
            state["bag"] = {c: 0 for c in TOKEN_COLORS}
            for leftover in pool:  # 25 枚宝石恰好 25 格，防御性保留
                state["bag"][leftover] += 1
            turn["refilled"] = True
            record.update(placed=placed)
            notes.append(f"补充宝石盘 {placed} 枚")
            notes.append("对手" + self._gain_privilege(state, _other(state, player_id)))
        elif action in MANDATORY_ACTIONS:
            if action == "pass":
                state["consecutive_passes"] += 1
                notes.append("三样必选都做不了，本回合 pass")
            else:
                state["consecutive_passes"] = 0
            if action == "take":
                taken = []
                for r, c in move["cells"]:
                    color = state["board"][r][c]
                    taken.append(color)
                    state["board"][r][c] = None
                    me["tokens"][color] += 1
                record.update(cells=move["cells"], gems=taken)
                notes.append("拿了 " + "·".join(COLOR_LABELS[c] for c in taken))
                if (len(taken) == 3 and len(set(taken)) == 1) or taken.count("pearl") == 2:
                    why = "两枚珍珠" if taken.count("pearl") == 2 else "三枚同色"
                    notes.append(f"一次拿了{why}，对手" + self._gain_privilege(
                        state, _other(state, player_id)))
            elif action == "reserve":
                r, c = move["gold"]
                state["board"][r][c] = None
                me["tokens"]["gold"] += 1
                if "card_id" in move:
                    card_id = move["card_id"]
                    level, index, revealed = self._replace_pyramid_slot(state, card_id)
                    me["reserved"].append({"id": card_id, "blind": False})
                    record.update(gold=[r, c], card_id=card_id, slot=[int(level), index])
                    notes.append(f"拿 1 枚金并保留场上的{_card_label(card_id)}")
                else:
                    level = move["level"]
                    card_id = state["decks"][str(level)].pop(0)
                    me["reserved"].append({"id": card_id, "blind": True})
                    record.update(gold=[r, c], level=level, blind=True)
                    notes.append(f"拿 1 枚金并从{LEVEL_LABELS[level]}牌堆盲抽保留 1 张")
            elif action == "buy":
                card_id = move["card_id"]
                card = CARDS[card_id]
                due = cost_after_bonus(state, player_id, card_id)
                paid: dict[str, int] = {}
                for color, need in due.items():
                    use = min(need, me["tokens"][color])
                    if use:
                        me["tokens"][color] -= use
                        state["bag"][color] += use
                        paid[color] = use
                short = sum(due.values()) - sum(paid.values())
                if short:
                    me["tokens"]["gold"] -= short
                    state["bag"]["gold"] += short
                    paid["gold"] = short
                reserved = next((item for item in me["reserved"] if item["id"] == card_id), None)
                if reserved is not None:
                    me["reserved"].remove(reserved)
                    record.update(source="reserved")
                else:
                    level, index, _revealed = self._replace_pyramid_slot(state, card_id)
                    record.update(source="pyramid", slot=[int(level), index])
                crowns_before = crowns(state, player_id)
                bought = {"id": card_id}
                if card["joker"]:
                    bought["as"] = move["joker_color"]
                me["cards"].append(bought)
                crowns_after = crowns(state, player_id)
                paid_text = _tokens_text(paid) if paid else "0 枚（加成全部抵扣）"
                label = _card_label(card_id)
                if card["joker"]:
                    label += f"（压在{COLOR_LABELS[move['joker_color']]}色上）"
                record.update(card_id=card_id, paid=paid)
                if card["joker"]:
                    record["joker_color"] = move["joker_color"]
                notes.append(f"买下{label}，付 {paid_text}")
                ability = card["ability"]
                if ability == "extra_turn":
                    turn["extra_turn"] = True
                elif ability == "take_gem":
                    turn["pending"].append({"kind": "take_gem", "color": card["color"]})
                elif ability == "steal":
                    turn["pending"].append({"kind": "steal"})
                elif ability == "privilege":
                    notes.append("卡牌能力：" + self._gain_privilege(state, player_id))
                for threshold in CROWN_THRESHOLDS:
                    if crowns_before < threshold <= crowns_after:
                        turn["pending"].append({"kind": "royal"})
            state["flow"]["phase"] = "resolve"
            if state["consecutive_passes"] >= 2:
                order = state["participant_order"]
                a, b = (prestige(state, pid) for pid in order)
                winner = order[0] if a > b else order[1] if b > a else None
                result = self._finish(state, winner, f"双方都无法行动，按声望 {a}:{b} 判定")
                self._record(state, record, effects, notes)
                return self._with_delta(
                    state, before,
                    MoveResult(state=state, note="；".join(n for n in notes if n),
                               result=deepcopy(result)),
                )
            complete = self._advance(state, player_id, notes, effects)
        else:  # pending choice
            item = turn["pending"][0]
            choice = move.get("cell") or move.get("gem") or move.get("royal_id")
            self._resolve_pending(state, player_id, item, choice, notes, effects)
            record.update({k: v for k, v in move.items() if k != "action"})
            complete = self._advance(state, player_id, notes, effects)

        self._record(state, record, effects, notes)
        if action in OPTIONAL_ACTIONS:
            result = MoveResult(state=state, retain_turn=True, note="；".join(notes))
        elif complete:
            result = self._end_turn(state, player_id, notes)
        else:
            result = MoveResult(
                state=state, retain_turn=True,
                note="；".join(n for n in notes + ["等待选择：" + self._pending_text(state, player_id)] if n),
            )
        return self._with_delta(state, before, result)

    @staticmethod
    def _record(
        state: dict[str, Any], record: dict[str, Any],
        effects: list[dict[str, Any]], notes: list[str],
    ) -> None:
        if effects:
            record["effects"] = effects
        record["note"] = "；".join(n for n in notes if n)
        state["last_action"] = deepcopy(record)
        history = state.setdefault("action_history", [])
        history.append(deepcopy(record))
        del history[:-HISTORY_LIMIT]

    # ------------------------------------------------------------------
    # MCP delta (compact encoding shared with bootstrap/full_state)

    def _delta_basis(self, state: dict[str, Any]) -> dict[str, Any]:
        return compact_public(self._project_public(state, terminal=False))

    DELTA_KEYS = (
        "board", "bag", "privileges_on_table", "deck_counts", "royals_available",
        "pending", "extra_turn", "refilled", "winner_player_id", "finish_reason",
    )

    def _with_delta(
        self, state: dict[str, Any], before: dict[str, Any], result: MoveResult
    ) -> MoveResult:
        after = self._delta_basis(state)
        delta: dict[str, Any] = {}
        for key in self.DELTA_KEYS:
            if before[key] != after[key]:
                delta[key] = after[key]
        if "board" in delta:
            cells = [[r, c, after["board"][r][c]] for r in range(5) for c in range(5)
                     if before["board"][r][c] != after["board"][r][c]]
            # One/two cells are shorter than repeating five rows. Refill and
            # larger changes retain the readily readable complete board.
            if len(cells) <= 2:
                delta.pop("board")
                delta["board_set"] = cells
        slots = [
            [int(level), index, card]
            for level in ("1", "2", "3")
            for index, card in enumerate(after["pyramid"][level])
            if card != before["pyramid"][level][index]
        ]
        if slots:
            delta["pyramid_set"] = slots
        players = {}
        for pid, value in after["players"].items():
            old = before["players"].get(pid, {})
            changed = {k: v for k, v in value.items() if old.get(k) != v}
            if "purchased" in changed:
                previous = old.get("purchased", [])
                if value["purchased"][:len(previous)] == previous:
                    changed["purchased_add"] = changed.pop("purchased")[len(previous):]
            if changed:
                players[pid] = changed
        if players:
            delta["players"] = players
        result.public_event = {"gem_merchant_delta": delta} if delta else None
        return result

    # ------------------------------------------------------------------
    # projections

    def _player_public(self, state: dict[str, Any], player_id: str, *, terminal: bool) -> dict[str, Any]:
        player = state["players"][player_id]
        reserved = []
        for item in player["reserved"]:
            if item["blind"] and not terminal:
                reserved.append({"hidden": True, "blind": True,
                                 "level": CARDS[item["id"]]["level"]})
            else:
                card = card_view(item["id"])
                card["blind"] = bool(item["blind"])
                reserved.append(card)
        per_color = color_points(state, player_id)
        return {
            "tokens": dict(player["tokens"]),
            "token_total": token_total(state, player_id),
            "privileges": player["privileges"],
            "points": prestige(state, player_id),
            "crowns": crowns(state, player_id),
            "bonuses": bonuses(state, player_id),
            "color_points": per_color,
            "best_color_points": max(per_color.values()),
            "purchased": [
                {"id": item["id"], **({"as": item["as"]} if item.get("as") else {})}
                for item in player["cards"]
            ],
            "reserved": reserved,
            "royals": [deepcopy(ROYALS[rid]) for rid in player["royals"]],
        }

    def _project_public(self, state: dict[str, Any], *, terminal: bool) -> dict[str, Any]:
        order = state["participant_order"]
        turn = state["turn_state"]
        pending = None
        if turn["pending"] and not self._is_finished(state):
            item = turn["pending"][0]
            pending = {"kind": item["kind"]}
            if item["kind"] == "take_gem":
                pending["color"] = item["color"]
            if item["kind"] == "discard" and state.get("turn_player_id"):
                pending["count"] = token_total(state, state["turn_player_id"]) - TOKEN_LIMIT
        return {
            "board_kind": self.game_type,
            "flow": deepcopy(state["flow"]),
            "participant_order": list(order),
            "first_player_id": state["first_player_id"],
            "turn_player_id": state["turn_player_id"],
            "board": deepcopy(state["board"]),
            "bag": dict(state["bag"]),
            "bag_count": _bag_count(state),
            "privileges_on_table": state["privileges_on_table"],
            "pyramid": {
                level: [card_view(cid) if cid else None for cid in state["pyramid"][level]]
                for level in ("1", "2", "3")
            },
            "deck_counts": {level: len(state["decks"][level]) for level in ("1", "2", "3")},
            "royals_available": [deepcopy(ROYALS[rid]) for rid in state["royals"]],
            "players": {pid: self._player_public(state, pid, terminal=terminal) for pid in order},
            "refilled": bool(turn["refilled"]),
            "extra_turn": bool(turn["extra_turn"]),
            "pending": pending,
            "consecutive_passes": state["consecutive_passes"],
            "winner_player_id": state["winner_player_id"],
            "finish_reason": state["finish_reason"],
            "result": deepcopy(state["result"]),
            "last_action": self._public_record(state["last_action"]),
            "action_history": [self._public_record(item) for item in state["action_history"][-20:]],
            "win_targets": {"points": WIN_POINTS, "crowns": WIN_CROWNS,
                            "color_points": WIN_COLOR_POINTS, "token_limit": TOKEN_LIMIT},
            "last_action_note": state.get("last_action_note", ""),
        }

    @staticmethod
    def _public_record(record):
        # Records are authored public: blind reservations store only the level.
        return deepcopy(record)

    def public_state(self, state, participants):
        del participants
        return self._project_public(state, terminal=self._is_finished(state))

    def terminal_public_state(self, state, participants):
        del participants
        return self._project_public(state, terminal=True)

    def private_state(self, state, viewer, participants):
        del participants
        player_id = str(viewer["player_id"])
        player = state["players"].get(player_id)
        if player is None:
            return {}
        reserved = []
        for item in player["reserved"]:
            card = card_view(item["id"])
            card["blind"] = bool(item["blind"])
            reserved.append(card)
        return {
            "reserved": reserved,
            "legal_actions": self._legal_actions_for(state, player_id),
        }

    def participant_summary(self, state, participant, participants):
        del participants
        player = (state.get("players") or {}).get(participant["player_id"])
        if not player:
            return {}
        return {"score": player["points"], "皇冠": player["crowns"]}

    def result_for(self, state, participants):
        del participants
        return deepcopy(state.get("result")) if state.get("result") else None

    def npc_legal_actions(self, state, actor, participants):
        """Authoritative actions for temporary timeout assistance only.

        There is no system NPC (``supports_npcs=False``). Assistance skips the
        optional privilege/refill steps unless refilling is the only option.
        """
        del participants
        actions = self._legal_actions_for(state, str(actor["player_id"]))
        core = [item for item in actions if item["action"] not in OPTIONAL_ACTIONS]
        return core or actions

    # MCP projections ---------------------------------------------------

    DELTA_FORMAT = (
        "编码：W白 U蓝 G绿 R红 K黑 P珍珠 O金。board 为 5 个字符串（行 0–4，字符下标为列 0–4，. 为空格）；"
        "bag/tokens/bonuses/color_points 形如 \"W2 U1\"（未列出的为 0）；"
        "卡牌形如 \"#59 L3 green+1 3pt 2crown steal =W5 U3 R3 P1\"：编号、等级、加成颜色+加成数"
        "（joker 为百搭，points-only 为纯分卡，joker-as-red 为压在红色上的百搭）、声望、皇冠、能力、成本；"
        "hidden L3 为对手盲抽的三级卡。gem_merchant_delta 只给本动作改变的字段："
        "players 按玩家 ID 与字段合并，purchased_add 将卡号追加到该玩家 purchased；"
        "board_set=[[row,col,字符]] 替换宝石盘对应格（. 清空），board 则整体替换；"
        "pyramid_set=[[level,index,card|null]] 替换金字塔对应格，其余键整体替换。"
    )

    def mcp_snapshot_state(self, public_state, viewer, participants):
        del viewer, participants
        snapshot = compact_public(public_state)
        for key in ("board_kind", "flow", "participant_order", "first_player_id",
                    "turn_player_id", "win_targets", "consecutive_passes", "result"):
            snapshot[key] = deepcopy(public_state.get(key))
        snapshot["royal_table"] = {
            str(rid): f"{r['name']} {r['points']}分 " + (ABILITY_LABELS[r["ability"]] if r["ability"] else "无能力")
            for rid, r in ROYALS.items()
        }
        snapshot["delta_format"] = self.DELTA_FORMAT
        return snapshot

    def mcp_bootstrap_state(self, public_state, viewer, participants):
        return self.mcp_snapshot_state(public_state, viewer, participants)

    def mcp_private_state(self, private_state, viewer, participants):
        del viewer, participants
        private = {"reserved": [card_code(card) for card in private_state.get("reserved", [])]}
        actions = private_state.get("legal_actions") or []
        if any(a["action"] in MANDATORY_ACTIONS | OPTIONAL_ACTIONS for a in actions):
            private["legal_summary"] = self._legal_summary(actions)
        else:
            private["legal_actions"] = deepcopy(actions)
        return private

    def mcp_turn_private_state(self, private, public):
        """Ordinary-only projection; all hidden faces remain viewer-private.

        Face-up reserves already live in the public snapshot/deltas. Keep the
        blind list authoritative, including an explicit empty list after buying
        the last blind card. Full recovery continues to return every card.
        """
        del public
        result = deepcopy(private)
        result["blind_reserved"] = [card for card in result.pop("reserved", [])
                                    if card.endswith(" blind")]
        summary = result.get("legal_summary", {})
        if "take" in summary:
            summary["take"] = int(summary["take"].split(" ", 1)[0])
        if "use_privilege" in summary:
            summary["use_privilege"] = True
        return result

    @staticmethod
    def _legal_summary(actions: list[dict[str, Any]]) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        if any(a["action"] == "use_privilege" for a in actions):
            summary["use_privilege"] = "cell=盘上任意非金宝石"
        if {"action": "refill"} in actions:
            summary["refill"] = True
        takes = sum(1 for a in actions if a["action"] == "take")
        if takes:
            summary["take"] = f"{takes} 种：1–3 枚同一横/竖/斜线上紧挨着的非金宝石"
        reserves = [a for a in actions if a["action"] == "reserve"]
        if reserves:
            summary["reserve"] = {
                "gold": [list(cell) for cell in sorted({tuple(a["gold"]) for a in reserves})],
                "card_ids": sorted({a["card_id"] for a in reserves if "card_id" in a}),
                "levels": sorted({a["level"] for a in reserves if "level" in a}),
            }
        buys: list[Any] = []
        for a in actions:
            if a["action"] != "buy":
                continue
            if "joker_color" not in a:
                buys.append(a["card_id"])
                continue
            entry = next((b for b in buys if isinstance(b, dict) and b["card_id"] == a["card_id"]), None)
            if entry is None:
                entry = {"card_id": a["card_id"], "joker_color": []}
                buys.append(entry)
            entry["joker_color"].append(a["joker_color"])
        if buys:
            summary["buy"] = buys
        if {"action": "pass"} in actions:
            summary["pass"] = True
        return summary

    def format_action(self, state, move, actor):
        del actor
        if not isinstance(move, dict):
            return "宝石商人行动"
        action = move.get("action")
        try:
            if action == "use_privilege":
                return f"用特权券拿 {_cell_label(_cell_value(move.get('cell')))}"
            if action == "refill":
                return "补充宝石盘"
            if action == "take":
                cells = [_cell_value(c) for c in move.get("cells") or []]
                colors = [state["board"][r][c] for r, c in cells]
                return "拿宝石 " + "·".join(COLOR_LABELS.get(c, "?") for c in colors)
            if action == "reserve":
                if "card_id" in move:
                    return f"拿金并保留 {_card_label(_int_value(move['card_id'], 'card_id'))}"
                return f"拿金并盲抽保留{LEVEL_LABELS.get(move.get('level'), '')}卡"
            if action == "buy":
                text = f"购买 {_card_label(_int_value(move['card_id'], 'card_id'))}"
                if move.get("joker_color") in COLOR_LABELS:
                    text += f"（百搭作{COLOR_LABELS[move['joker_color']]}）"
                return text
            if action == "pass":
                return "无事可做，pass"
            if action == "take_bonus_gem":
                return f"能力：拿 {_cell_label(_cell_value(move.get('cell')))}"
            if action == "steal":
                return f"从对手拿 1 枚{COLOR_LABELS.get(move.get('gem'), '')}"
            if action == "choose_royal":
                royal = ROYALS.get(move.get("royal_id"))
                return f"选称号卡「{royal['name']}」" if royal else "选称号卡"
            if action == "discard":
                return f"弃 1 枚{COLOR_LABELS.get(move.get('gem'), '')}"
        except (KeyError, TypeError, ValueError, IndexError):
            pass
        return "宝石商人行动"

    def format_move(self, state, move, mark):
        del mark
        return self.format_action(state, move, {})
