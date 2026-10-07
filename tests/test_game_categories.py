"""Catalog categories are discovery metadata, never persisted game identity."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import database, framework
from app.games import GAMES, GAME_CATEGORIES, game_catalog


class GameCategoryTests(unittest.TestCase):
    def test_four_categories_and_four_tabletop_games(self):
        self.assertEqual(GAME_CATEGORIES, {'board', 'card', 'dice', 'tabletop'})
        categories = {g['game_type']: g['category'] for g in game_catalog()}
        expected = {
            'board': {'aeroplane_chess', 'banqi', 'checkers', 'chess', 'chinese_checkers',
                      'connect4', 'dots_boxes', 'go', 'gomoku', 'jungle', 'junqi',
                      'othello', 'tictactoe', 'xiangqi'},
            'card': {'blackjack', 'doudizhu', 'gandengyan', 'guandan', 'mahjong',
                     'texas_holdem', 'train_cards', 'uno', 'zhajinhua'},
            'dice': {'liars_dice', 'yahtzee'},
            'tabletop': {'bomb_plane', 'monopoly', 'rummikub', 'carcassonne', 'gem_merchant', 'monopoly_plus'},
        }
        self.assertEqual({category: {g for g, c in categories.items() if c == category}
                          for category in GAME_CATEGORIES}, expected)
        with patch.object(GAMES['rummikub'], 'category', 'unknown'):
            with self.assertRaisesRegex(ValueError, 'board/card/dice/tabletop'):
                game_catalog()

    def test_old_category_rooms_restore_play_and_rematch_without_migration(self):
        with tempfile.TemporaryDirectory(prefix='duel-categories-') as temporary:
            with patch.object(database, 'DB_PATH', Path(temporary) / 'test.db'):
                database.init_db()
                for game_type, old_category in [('rummikub', 'card'), ('monopoly', 'board')]:
                    with self.subTest(game_type=game_type):
                        human, ai = f'{game_type}-human', f'{game_type}-ai'
                        with patch.object(GAMES[game_type], 'category', old_category):
                            old = framework.create_room(game_type, 'human_first', 'human', human,
                                                        ai, require_confirmations=False)
                        saved = deepcopy(old['board_state'])
                        room = framework.get_room(old['room_id'])
                        self.assertEqual(room['board_state'], saved)
                        self.assertEqual(room['game_type'], game_type)
                        viewer = framework.project_room_for_viewer(room, human)
                        if game_type == 'rummikub':
                            self.assertEqual(len(viewer['private_state']['hand']), 14)
                            self.assertNotIn('hands', viewer['board_state'])
                            self.assertNotIn('pool', viewer['board_state'])
                            move = {'action': 'draw'}
                        else:
                            self.assertNotIn('_decks', viewer['board_state'])
                            move = {'action': 'roll', 'action_seq': saved['action_seq']}
                        room = framework.play_move(room['room_id'], 'human', human, move,
                                                   expected_revision=room['revision'])
                        self.assertEqual(room['revision'], old['revision'] + 1)
                        room = framework.resign(room['room_id'], 'human', human)
                        self.assertEqual(room['status'], 'finished')
                        rematch = framework.create_room(game_type, 'human_first', 'ai', ai, human,
                                                        require_confirmations=False,
                                                        rematch_of_room_id=room['room_id'])
                        self.assertEqual(rematch['game_type'], game_type)
                        self.assertEqual(rematch['rematch_of_room_id'], room['room_id'])
                        self.assertNotEqual(rematch['room_id'], room['room_id'])
