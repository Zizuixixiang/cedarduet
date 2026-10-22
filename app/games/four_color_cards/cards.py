"""The physical 112-card deck shared by the three requested variants."""

COLORS = ("R", "Y", "G", "W")
COLOR_NAMES = dict(zip(COLORS, ("红", "黄", "绿", "白")))
RED_NAMES = ("帥", "仕", "相", "俥", "傌", "炮", "兵")
BLACK_NAMES = ("將", "士", "象", "車", "馬", "包", "卒")
CODES = tuple(f"{color}{rank}" for color in COLORS for rank in range(7))
CODE_INDEX = {code: index for index, code in enumerate(CODES)}
DECK = tuple(f"{code}-{copy}" for code in CODES for copy in range(1, 5))
CARD_CODES = {card: card.split("-")[0] for card in DECK}


def code(card):
    if not isinstance(card, str) or card not in CARD_CODES:
        raise ValueError("无效四色牌实体 ID")
    return CARD_CODES[card]


def validate_cards(cards):
    if not isinstance(cards, (list, tuple)):
        raise ValueError("牌必须为实体 ID 数组")
    for card in cards:
        code(card)
    if len(set(cards)) != len(cards):
        raise ValueError("同一张实体牌不能重复使用")


def label(card):
    value = code(card)
    names = RED_NAMES if value[0] in ("R", "Y") else BLACK_NAMES
    return COLOR_NAMES[value[0]] + names[int(value[1])]


def card_view(card):
    return {"id": card, "label": label(card)}
