"""Texas Hold'em hand evaluation from vendored PyPokerEngine.

The upstream evaluator selected the right broad category but encoded the two
hole-card ranks as universal tie breakers. That can award a board-only hand to
the player with a higher irrelevant hole card. This vendored hardening keeps
PyPokerEngine's public API and category constants while comparing the complete
best five-card combination with all category-specific kickers.
"""

from collections import Counter
from itertools import combinations


class HandEvaluator:

  HIGHCARD      = 0
  ONEPAIR       = 1 << 8
  TWOPAIR       = 1 << 9
  THREECARD     = 1 << 10
  STRAIGHT      = 1 << 11
  FLASH         = 1 << 12
  FULLHOUSE     = 1 << 13
  FOURCARD      = 1 << 14
  STRAIGHTFLASH = 1 << 15

  HAND_STRENGTH_MAP = {
      HIGHCARD: "HIGHCARD",
      ONEPAIR: "ONEPAIR",
      TWOPAIR: "TWOPAIR",
      THREECARD: "THREECARD",
      STRAIGHT: "STRAIGHT",
      FLASH: "FLASH",
      FULLHOUSE: "FULLHOUSE",
      FOURCARD: "FOURCARD",
      STRAIGHTFLASH: "STRAIGHTFLASH"
  }
  _STRENGTHS = (
      HIGHCARD, ONEPAIR, TWOPAIR, THREECARD, STRAIGHT,
      FLASH, FULLHOUSE, FOURCARD, STRAIGHTFLASH,
  )

  @classmethod
  def gen_hand_rank_info(cls, hole, community):
    hand = cls.eval_hand(hole, community)
    tie_ranks = cls._unpack_tie_ranks(hand)
    hole_ranks = sorted((card.rank for card in hole), reverse=True)
    return {
        "hand": {
          "strength": cls.HAND_STRENGTH_MAP[cls.__mask_hand_strength(hand)],
          "high": tie_ranks[0],
          "low": tie_ranks[1],
          "kickers": [rank for rank in tie_ranks[1:] if rank],
        },
        "hole": {
          "high": hole_ranks[0],
          "low": hole_ranks[1],
        }
    }

  @classmethod
  def eval_hand(cls, hole, community):
    cards = list(hole) + list(community)
    if len(hole) != 2 or not 5 <= len(cards) <= 7:
      raise ValueError("Texas Hold'em evaluation needs 2 hole and 3-5 board cards")
    category, tie_ranks = max(
        cls._score_five(combo) for combo in combinations(cards, 5)
    )
    padded = tuple(tie_ranks) + (0,) * (5 - len(tie_ranks))
    score = category << 20
    for index, rank in enumerate(padded[:5]):
      score |= rank << (16 - index * 4)
    return score

  @classmethod
  def _score_five(cls, cards):
    ranks = [card.rank for card in cards]
    counts = Counter(ranks)
    groups = sorted(
        ((count, rank) for rank, count in counts.items()), reverse=True
    )
    flush = len({card.suit for card in cards}) == 1
    unique = set(ranks)
    if 14 in unique:
      unique.add(1)
    straight_high = 0
    for high in range(14, 4, -1):
      if all(rank in unique for rank in range(high - 4, high + 1)):
        straight_high = high
        break
    if flush and straight_high:
      return 8, (straight_high,)
    if groups[0][0] == 4:
      quad = groups[0][1]
      kicker = max(rank for rank in ranks if rank != quad)
      return 7, (quad, kicker)
    if groups[0][0] == 3 and groups[1][0] == 2:
      return 6, (groups[0][1], groups[1][1])
    if flush:
      return 5, tuple(sorted(ranks, reverse=True))
    if straight_high:
      return 4, (straight_high,)
    if groups[0][0] == 3:
      trip = groups[0][1]
      kickers = sorted(
          (rank for rank in ranks if rank != trip), reverse=True
      )
      return 3, (trip, *kickers)
    pairs = sorted(
        (rank for rank, count in counts.items() if count == 2), reverse=True
    )
    if len(pairs) == 2:
      kicker = next(rank for rank, count in counts.items() if count == 1)
      return 2, (pairs[0], pairs[1], kicker)
    if len(pairs) == 1:
      pair = pairs[0]
      kickers = sorted(
          (rank for rank in ranks if rank != pair), reverse=True
      )
      return 1, (pair, *kickers)
    return 0, tuple(sorted(ranks, reverse=True))

  @classmethod
  def _unpack_tie_ranks(cls, score):
    return tuple((score >> shift) & 15 for shift in (16, 12, 8, 4, 0))

  @classmethod
  def __mask_hand_strength(cls, score):
    return cls._STRENGTHS[(score >> 20) & 15]

  @classmethod
  def __mask_hand_high_rank(cls, score):
    return cls._unpack_tie_ranks(score)[0]

  @classmethod
  def __mask_hand_low_rank(cls, score):
    return cls._unpack_tie_ranks(score)[1]

  @classmethod
  def __mask_hole_high_rank(cls, score):
    del score
    return 0

  @classmethod
  def __mask_hole_low_rank(cls, score):
    del score
    return 0
