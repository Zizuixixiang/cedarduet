import json
import random
from copy import deepcopy
import unittest

from app.games import GAMES
from app.games.base import GamePlugin


# Final hidden-information audit. Keeping every catalog entry in exactly one
# bucket makes a newly added game fail this test until its terminal policy has
# been reviewed instead of silently inheriting an unsuitable reveal rule.
FULL_TERMINAL_REVIEW_REASONS = {
    "bomb_plane": "opponent planes remain hidden until the terminal participant-only review",
    "banqi": "unflipped piece identities become reviewable only after the room is terminal",
    "blackjack": "the dealer hole is hidden during play and revealed after settlement or room termination",
    "doudizhu": "opponent hands stay private during play and remaining hands are shown at terminal",
    "gem_merchant": "blind-reserved card faces stay private during play and are reviewable at terminal; deck order never leaves the server",
    "gandengyan": "opponent hands stay private during play and remaining hands are shown at terminal",
    "guandan": "opponent hands stay private during play and remaining hands are shown at terminal",
    "junqi": "unrevealed ranks stay private during play and the terminal board is reviewable",
    "liars_dice": "current dice stay private during bidding and terminal dice are reviewable",
    "ka_wu_xing": "liang exposes only the fixed hand/waits during play; remaining hands and concealed kongs are reviewable at terminal, never the undrawn wall",
    "mahjong": "concealed hands and concealed-kong faces become reviewable only at terminal",
    "rummikub": "remaining entity racks are revealed only by terminal projection; ordinary public/private views and pool order stay private",
    "train_cards": "future personal pile order stays private during play and is reviewable at terminal",
    "uno": "opponent hands stay private during play and remaining hands are shown at terminal",
}

RULE_SCOPED_REVEAL_REASONS = {
    "four_color_tenhu": "exposed melds/discards/offers and rule-scoped winning/scoring cards are public; other concealed cards and unrevealed wall stay private at terminal",
    "four_color_jiuzhi": "exposed melds/discards/offers and rule-scoped winning/scoring cards are public; other concealed cards and unrevealed wall stay private at terminal",
    "four_color_qunhu": "exposed melds/discards/offers and rule-scoped winning/scoring cards are public; other concealed cards and unrevealed wall stay private at terminal",
    "carcassonne": "placed/current terrain is public; the server deck order stays hidden even after resignation or terminal scoring",
    "monopoly": "cash, assets and revealed events are public; future decks, internal continuations and held jail-card sources stay hidden even at terminal",
    "monopoly_plus": "cash, assets, bets, loans and revealed events are public; held item cards show only a count and, like future decks and bus tickets order, stay hidden even at terminal",
    "texas_holdem": "showdown holes are public, but fold/muck endings must not force a reveal",
    "zhajinhua": "forced showdown hands are public, but folded or unshown hands must stay hidden",
}

PUBLIC_INFORMATION_REASONS = {
    "aeroplane_chess": "the roll and every plane position are public after each action",
    "checkers": "the complete board is public",
    "chess": "the complete board is public",
    "chinese_checkers": "the complete board is public",
    "connect4": "the complete board is public",
    "dots_boxes": "all edges and claimed boxes are public",
    "go": "the board, history-dependent legality, and score confirmations contain no opponent secret",
    "gomoku": "the complete board is public",
    "jungle": "the complete board is public",
    "othello": "the complete board is public",
    "tictactoe": "the complete board is public",
    "xiangqi": "the complete board is public",
    "yahtzee": "the active roll, held dice, scorecards, and score previews are intentionally public",
}


class HiddenInformationCatalogAuditTests(unittest.TestCase):
    def test_every_catalog_game_has_an_explicit_terminal_privacy_classification(self):
        buckets = (
            set(FULL_TERMINAL_REVIEW_REASONS),
            set(RULE_SCOPED_REVEAL_REASONS),
            set(PUBLIC_INFORMATION_REASONS),
        )
        self.assertFalse(buckets[0] & buckets[1])
        self.assertFalse(buckets[0] & buckets[2])
        self.assertFalse(buckets[1] & buckets[2])
        self.assertEqual(set(GAMES), set().union(*buckets))
        self.assertTrue(all(
            isinstance(reason, str) and reason
            for reasons in (
                FULL_TERMINAL_REVIEW_REASONS,
                RULE_SCOPED_REVEAL_REASONS,
                PUBLIC_INFORMATION_REASONS,
            )
            for reason in reasons.values()
        ))

    def test_full_review_games_override_the_safe_default_terminal_projection(self):
        for game_type in FULL_TERMINAL_REVIEW_REASONS:
            with self.subTest(game_type=game_type):
                self.assertIsNot(
                    type(GAMES[game_type]).terminal_public_state,
                    GamePlugin.terminal_public_state,
                )

    def test_poker_keeps_safe_default_terminal_projection(self):
        for game_type in ("texas_holdem", "zhajinhua"):
            with self.subTest(game_type=game_type):
                self.assertIs(
                    type(GAMES[game_type]).terminal_public_state,
                    GamePlugin.terminal_public_state,
                )

    def test_ka_wu_xing_terminal_review_reveals_hands_and_kongs_but_never_wall(self):
        game = type(GAMES['ka_wu_xing'])(random.Random(37))
        people = [{'player_id': f'audit-{i}', 'role': 'human'} for i in range(3)]
        initial = game.initialize(people)
        # A physical 84-tile fixture with one concealed kong and a live wall.
        tiles = game.build_wall()
        kong = [t for t in tiles if t['code'] == 'T9']
        remaining = [t for t in tiles if t['code'] != 'T9']
        random.Random(37).shuffle(remaining)
        for person, count in zip(people, (14, 10, 13)):
            initial['hands'][person['player_id']] = remaining[:count]
            del remaining[:count]
        initial['wall'] = remaining
        initial['melds']['audit-1'] = [dict(kind='concealed_gang', tiles=kong,
            engine_tile='T9', offer=0, source_player_id=None)]
        initial['drawn_tile_id'] = initial['hands']['audit-0'][-1]['id']

        for ending in ('playing', 'draw', 'resignation', 'archive'):
            state = deepcopy(initial)
            if ending == 'draw':
                # Exhaust the wall through public discards for the draw fixture.
                state['discards']['audit-2'].extend(state['wall'])
                state['wall'] = []
                game._finish_draw(state)
            elif ending == 'resignation':
                game.apply_resignation(state, 'audit-0', people)
            before = deepcopy(state)
            terminal = ending != 'playing'
            public = (game.terminal_public_state if terminal else game.public_state)(state, people)
            with self.subTest(ending=ending):
                self.assertNotIn('wall', public)
                self.assertNotIn('hands', public)
                self.assertEqual('terminal_hands' in public, terminal)
                if terminal:
                    for pid, hand in state['hands'].items():
                        self.assertCountEqual([t['id'] for t in public['terminal_hands'][pid]],
                                              [t['id'] for t in hand])
                    self.assertEqual(public['melds']['audit-1'][0]['tiles'],
                                     [game._public_tile(t) for t in kong])
                for viewer in people:
                    pid = viewer['player_id']
                    private = game.private_state(state, viewer, people)
                    self.assertCountEqual([t['id'] for t in private['hand']],
                                          [t['id'] for t in state['hands'][pid]])
                    encoded = json.dumps({'public': public, 'private': private})
                    secrets = list(state['wall'])
                    if not terminal:
                        secrets += [t for other, hand in state['hands'].items()
                                    if other != pid for t in hand]
                        if pid != 'audit-1':
                            secrets += kong
                    for tile in secrets:
                        self.assertNotIn(json.dumps(tile['id']), encoded)
                self.assertEqual(state, before, 'projection must not mutate the save')

    def test_four_color_departure_does_not_reveal_hands_or_future_draws(self):
        for name in ("four_color_tenhu", "four_color_jiuzhi", "four_color_qunhu"):
            game = type(GAMES[name])(random.Random(37))
            participants = [{"player_id": f"audit-{i}", "role": "human"}
                            for i in range(game.min_players)]
            initial = game.initialize(participants)
            terminal = deepcopy(initial)
            game.apply_resignation(terminal, participants[0]["player_id"], participants)
            for state, finished in ((initial, False), (terminal, True)):
                with self.subTest(game=name, terminal=finished):
                    before = deepcopy(state)
                    public = (game.terminal_public_state if finished else game.public_state)(state, participants)
                    self.assertNotIn("hands", public)
                    self.assertNotIn("wall", public)
                    self.assertNotIn("reserve", public)
                    for viewer in participants:
                        pid = viewer["player_id"]
                        private = game.private_state(state, viewer, participants)
                        self.assertEqual({c["id"] for c in private["hand"]}, set(state["hands"][pid]))
                        encoded = json.dumps({"public": public, "private": private})
                        secrets = list(state["wall"]) + list(state.get("reserve", []))
                        for other, hand in state["hands"].items():
                            if other != pid:
                                secrets += hand + sum(state.get("closed_pairs", {}).get(other, []), [])
                        for card in secrets:
                            self.assertNotIn(json.dumps(card), encoded)
                        if finished:
                            self.assertEqual(private["legal_actions"], [])
                    self.assertEqual(state, before)

    def test_monopoly_terminal_and_viewer_projections_keep_future_information_hidden(self):
        game = GAMES["monopoly"]
        participants = [{"player_id": f"audit-{i}", "role": "human",
                         "display_name": f"玩家{i}"} for i in range(4)]
        state = game.initialize(participants)
        state["players"][0]["jail_cards"] = ["chance"]
        state["_decks"]["chance"].remove(0)
        before = deepcopy(state)

        def assert_no_internal(value):
            if isinstance(value, dict):
                self.assertFalse(any(k.startswith("_") for k in value))
                self.assertFalse({"rng_state", "random_state", "seed"} & value.keys())
                for child in value.values():
                    assert_no_internal(child)
            elif isinstance(value, list):
                for child in value:
                    assert_no_internal(child)

        for terminal in (False, True):
            with self.subTest(terminal=terminal):
                public = (game.terminal_public_state if terminal else game.public_state)(state, participants)
                assert_no_internal(public)
                self.assertTrue(all("jail_cards" not in p for p in public["players"]))
                self.assertEqual([p["cash"] for p in public["players"]], [1500] * 4)
                self.assertEqual(len(public["tiles"]), 40)
                if terminal:
                    self.assertEqual(public["phase"], "finished")
                    self.assertEqual(public["legal_actions"], [])
                for i, viewer in enumerate(participants):
                    private = game.private_state(state, viewer, participants)
                    assert_no_internal(private)
                    self.assertEqual(private["jail_cards"], 1 if i == 0 else 0)
                    self.assertTrue(all("jail_cards" not in p for p in private["decision_context"]["players"]))
        self.assertEqual(state, before, "projection must not alter persisted decks or held cards")


if __name__ == "__main__":
    unittest.main()
