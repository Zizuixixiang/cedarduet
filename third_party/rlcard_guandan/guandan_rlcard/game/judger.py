"""Guandan judger: generates every combo a hand can legally play.

Rule fixes relative to the original implementation are marked with
``RULE`` comments:

* AA2233 is generated even when the hand also holds a K pair (the
  original loop ``break``-ed at K and never reached A).
* AAA222 (ace-low plate) is generated (the original loop stopped at A).
* A trips can carry a joker pair as its attached pair (999+BB).
* Duplicate pair actions differing only in card order are deduplicated.
* Bombs may use up to 10 cards (8 natural copies + 2 wildcards).
"""

import copy
import itertools
from collections import Counter, defaultdict

from guandan_rlcard.constants import (
    BIG_JOKER,
    CARD_RANK,
    CARD_RANK_INDEX,
    SMALL_JOKER,
)
from guandan_rlcard.game.card_utils import cards2str, count_wildcards

MAX_BOMB_SIZE = 10  # 8 natural copies + 2 wildcards
STRAIGHT_LENGTH = 5


class GuandanJudger:
    """Maintains the playable-action cache for the four players."""

    def __init__(self, players, level_rank_index):
        self.playable_actions = [[] for _ in range(4)]
        self._rebuild(players, level_rank_index)

    def reset(self, players, level_rank_index):
        """Recompute every player's playable actions for a new deal."""
        self._rebuild(players, level_rank_index)

    def _rebuild(self, players, level_rank_index):
        for player in players:
            player.set_current_hand(player.current_hand)
            self.playable_actions[player.player_id] = \
                self.playable_actions_from_hand(player.current_hand,
                                                level_rank_index)

    # ------------------------------------------------------------------
    # Action generation
    # ------------------------------------------------------------------

    @staticmethod
    def playable_actions_from_hand(current_hand, level_rank_index):
        """Enumerate every combo the given hand can play.

        Args:
            current_hand (list): Card objects in the player's hand.
            level_rank_index (int): index of the level rank in CARD_RANK.

        Returns:
            list: actions ``[combo_type, key_rank, cards]`` (no PASS).
        """
        stats = _HandStats(current_hand, level_rank_index)
        actions = []

        actions.extend(_gen_singles(stats))
        pair_actions, pair_combs, joker_pairs = _gen_pairs(stats)
        actions.extend(pair_actions)
        trips_actions, trips_combs = _gen_trips(stats)
        actions.extend(trips_actions)
        actions.extend(_gen_three_pairs(stats, pair_combs))
        actions.extend(_gen_three_with_two(stats, trips_combs,
                                           pair_combs, joker_pairs))
        actions.extend(_gen_two_trips(stats, trips_combs))
        actions.extend(_gen_bombs(stats))
        straights, straight_flushes = _gen_straights(stats)
        actions.extend(straights)
        actions.extend(straight_flushes)
        return actions

    # ------------------------------------------------------------------
    # Cache maintenance
    # ------------------------------------------------------------------

    def calc_playable_actions(self, player):
        """Drop cached actions no longer playable from the player's hand."""
        hand_counter = Counter(cards2str(player.current_hand))
        legal_actions = []
        for action in self.playable_actions[player.player_id]:
            card_counter = Counter(action[2])
            if all(hand_counter[c] >= n for c, n in card_counter.items()):
                legal_actions.append(action)
        self.playable_actions[player.player_id] = legal_actions
        return legal_actions

    def get_playable_actions(self, player):
        """Return the cached playable actions of the player."""
        return self.playable_actions[player.player_id]

    @staticmethod
    def judge_player_out(player):
        """Whether the player has emptied their hand."""
        return len(player.current_hand) == 0


class _HandStats:
    """Precomputed per-hand counters shared by the generators."""

    def __init__(self, current_hand, level_rank_index):
        self.level_rank_index = level_rank_index
        self.wildcard = 'H' + CARD_RANK[level_rank_index]

        self.card_counts = defaultdict(int)       # 'H3' -> count
        rank_counts = defaultdict(int)             # rank char -> count
        # rank char -> card strings of that rank, wildcards excluded
        self.suit_cards = {rank: [] for rank in CARD_RANK[:-2]}

        for card in current_hand:
            if card.suit == 'BJ':
                self.card_counts[SMALL_JOKER] += 1
                rank_counts['B'] += 1
            elif card.suit == 'RJ':
                self.card_counts[BIG_JOKER] += 1
                rank_counts['R'] += 1
            else:
                card_str = card.suit + card.rank
                self.card_counts[card_str] += 1
                rank_counts[card.rank] += 1
                if card_str != self.wildcard:
                    self.suit_cards[card.rank].append(card_str)

        self.rank_counts = [rank_counts[rank] for rank in CARD_RANK]
        self.wildcard_count = self.card_counts[self.wildcard]

    def rank_pool(self, rank_index, max_wildcards):
        """Cards usable to build a same-rank combo at ``rank_index``,
        wildcards appended up to ``max_wildcards``."""
        rank = CARD_RANK[rank_index]
        pool = copy.deepcopy(self.suit_cards[rank])
        if self.wildcard_count >= 1:
            if rank == CARD_RANK[self.level_rank_index]:
                # Heart level cards ARE this rank; add them all.
                pool.extend([self.wildcard] * self.wildcard_count)
            else:
                pool.extend([self.wildcard] *
                            min(max_wildcards, self.wildcard_count))
        return pool


def _gen_singles(stats):
    actions = []
    for card_str, num in stats.card_counts.items():
        if num == 0:
            continue
        if card_str == SMALL_JOKER:
            actions.append(['Single', 'B', [SMALL_JOKER]])
        elif card_str == BIG_JOKER:
            actions.append(['Single', 'R', [BIG_JOKER]])
        else:
            actions.append(['Single', card_str[-1], [card_str]])
    return actions


def _gen_pairs(stats):
    """Generate pair actions.

    Returns:
        tuple: (actions, pair_combs, joker_pairs) where pair_combs maps
        normal rank -> list of pairs and joker_pairs maps 'B'/'R' ->
        list of joker pairs (kept separate: jokers can join a full house
        but never a three-pair sequence).
    """
    actions = []
    pair_combs = defaultdict(list)
    joker_pairs = {}
    for i, count in enumerate(stats.rank_counts):
        if i < 13 and count + stats.wildcard_count >= 2:
            # A pair uses at most 1 wildcard (a pure wildcard pair is a
            # level pair, covered by the level-rank branch of rank_pool).
            pool = stats.rank_pool(i, max_wildcards=1)
            # RULE: dedupe by sorted card tuple; the original kept both
            # orderings of the same two cards as distinct actions.
            combs = set(tuple(sorted(c))
                        for c in itertools.combinations(pool, 2))
            for comb in combs:
                actions.append(['Pair', CARD_RANK[i], list(comb)])
                pair_combs[CARD_RANK[i]].append(list(comb))
        elif i >= 13 and count == 2:
            card_str = SMALL_JOKER if i == 13 else BIG_JOKER
            actions.append(['Pair', CARD_RANK[i], [card_str, card_str]])
            joker_pairs[CARD_RANK[i]] = [[card_str, card_str]]
    return actions, dict(pair_combs), joker_pairs


def _gen_trips(stats):
    actions = []
    trips_combs = defaultdict(list)
    for i, count in enumerate(stats.rank_counts):
        if i < 13 and count + stats.wildcard_count >= 3:
            pool = stats.rank_pool(i, max_wildcards=stats.wildcard_count)
            combs = set(tuple(sorted(c))
                        for c in itertools.combinations(pool, 3))
            for comb in combs:
                actions.append(['Trips', CARD_RANK[i], list(comb)])
                trips_combs[CARD_RANK[i]].append(list(comb))
    return actions, dict(trips_combs)


def _gen_three_pairs(stats, pair_combs):
    """Three consecutive pairs (三连对), A low or high, no K-A-2 wrap."""
    actions = []
    for key, pairs in pair_combs.items():
        if key == 'K':
            # RULE: K cannot start a three-pair (K-A-2 does not wrap),
            # but generation must continue so that AA2233 is still
            # produced (the original ``break`` skipped A entirely).
            continue
        index = CARD_RANK_INDEX[key]
        rank1 = CARD_RANK[(index + 1) % 13]  # A wraps to 2 for AA2233
        rank2 = CARD_RANK[(index + 2) % 13]
        if rank1 not in pair_combs or rank2 not in pair_combs:
            continue
        for base in pairs:
            used = count_wildcards(base, stats.level_rank_index)
            for second in pair_combs[rank1]:
                used2 = used + count_wildcards(second, stats.level_rank_index)
                if used2 > stats.wildcard_count:
                    continue
                for third in pair_combs[rank2]:
                    used3 = used2 + count_wildcards(third,
                                                    stats.level_rank_index)
                    if used3 > stats.wildcard_count:
                        continue
                    actions.append(['ThreePair', key, base + second + third])
    return actions


def _gen_three_with_two(stats, trips_combs, pair_combs, joker_pairs):
    """Full house (三带二). The attached pair may be a joker pair."""
    actions = []
    if not trips_combs:
        return actions
    # RULE: include joker pairs in the candidate pair pool (the original
    # never offered e.g. 999+BB even though it is legal).
    pair_pool = dict(pair_combs)
    pair_pool.update(joker_pairs)
    for key, trips in trips_combs.items():
        for triple in trips:
            used = count_wildcards(triple, stats.level_rank_index)
            for pair_rank, pairs in pair_pool.items():
                if pair_rank == key:
                    continue  # same rank would form a bomb instead
                for pair in pairs:
                    if used + count_wildcards(pair, stats.level_rank_index) \
                            > stats.wildcard_count:
                        continue
                    actions.append(['ThreeWithTwo', key, triple + pair])
    return actions


def _gen_two_trips(stats, trips_combs):
    """Plate (钢板): two consecutive trips, including KKKAAA and AAA222."""
    actions = []
    for key, trips in trips_combs.items():
        index = CARD_RANK_INDEX[key]
        # RULE: A wraps to 2 so that AAA222 is generated (the original
        # ``break``-ed at A and had no wrap).
        next_rank = CARD_RANK[(index + 1) % 13]
        if next_rank not in trips_combs:
            continue
        for base in trips:
            used = count_wildcards(base, stats.level_rank_index)
            for second in trips_combs[next_rank]:
                if used + count_wildcards(second, stats.level_rank_index) \
                        > stats.wildcard_count:
                    continue
                actions.append(['TwoTrips', key, base + second])
    return actions


def _gen_bombs(stats):
    """Bombs of 4 to MAX_BOMB_SIZE same-rank cards, plus the joker bomb."""
    actions = []
    for i, count in enumerate(stats.rank_counts):
        if i < 13 and count + stats.wildcard_count >= 4:
            pool = stats.rank_pool(i, max_wildcards=stats.wildcard_count)
            # RULE: cap raised from 8 to 10 (8 natural copies of a rank
            # plus both wildcards form a legal 10-card bomb).
            for size in range(4, min(len(pool), MAX_BOMB_SIZE) + 1):
                combs = set(tuple(sorted(c))
                            for c in itertools.combinations(pool, size))
                for comb in combs:
                    actions.append(['Bomb', CARD_RANK[i], list(comb)])
    # Four-joker bomb (天王炸).
    if stats.rank_counts[13] == 2 and stats.rank_counts[14] == 2:
        actions.append(['Bomb', 'R',
                        [BIG_JOKER, BIG_JOKER, SMALL_JOKER, SMALL_JOKER]])
    return actions


def _gen_straights(stats):
    """Five-card straights and straight flushes, A low or high.

    Wildcards may fill any missing position. A straight whose natural
    cards share one suit is emitted as a StraightFlush (the cards
    physically form one, so it cannot be declared as a plain straight).
    """
    straights = []
    straight_flushes = []

    suit_cards = {rank: list(cards) for rank, cards in stats.suit_cards.items()}
    if stats.wildcard_count > 0:
        for rank in CARD_RANK[:-2]:
            suit_cards[rank].append(stats.wildcard)

    # start -1 encodes the ace-low straight A-2-3-4-5.
    for start in range(-1, 9):
        required = [12 if r == -1 else r for r in range(start, start + 5)]
        candidates = [suit_cards[CARD_RANK[r]] or [stats.wildcard]
                      for r in required]

        missing = sum(1 for r in required if not stats.suit_cards[CARD_RANK[r]])
        total = sum(len(stats.suit_cards[CARD_RANK[r]]) for r in required)
        if missing > stats.wildcard_count \
                or total + stats.wildcard_count < STRAIGHT_LENGTH:
            continue

        key_rank = 'A' if start == -1 else CARD_RANK[start]
        for product in set(itertools.product(*candidates)):
            used_wildcards = 0
            cards = []
            suits = set()
            for card in product:
                if card == stats.wildcard:
                    used_wildcards += 1
                    if used_wildcards > stats.wildcard_count:
                        break
                else:
                    suits.add(card[0])
                cards.append(card)
            else:
                if len(cards) != STRAIGHT_LENGTH:
                    continue
                if len(suits) > 1:
                    straights.append(['Straight', key_rank, cards])
                else:
                    straight_flushes.append(['StraightFlush', key_rank, cards])
    return straights, straight_flushes
