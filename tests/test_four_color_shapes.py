"""Evidence-backed shapes are not evidence that the two blocked modes work."""
import unittest
from app.games.four_color_cards.jiuzhi_rules import winning_pairs
from app.games.four_color_cards.qunhu_rules import winning_partition


class IndependentShapes(unittest.TestCase):
    def test_jiuzhi_five_pairs_not_ten_pairs(self):
        hand = [f'{c}-{i}' for c in ('R0','R6','G3','W4','Y5') for i in (1,2)]
        self.assertEqual(len(winning_pairs(hand)), 5)
        self.assertIsNone(winning_pairs(hand[:-1]))
        self.assertIsNone(winning_pairs(hand + [f'{c}-{i}' for c in ('R0','R6','G3','W4','Y5') for i in (3,4)]))
        hand[-1] = 'G5-2'
        self.assertIsNone(winning_pairs(hand))

    def test_jiuzhi_quad_forms_two_pairs_and_entities_are_unique(self):
        hand = ['R0-1','R0-2','R0-3','R0-4','G6-1','G6-2','G6-3','G6-4','W5-1','W5-2']
        pairs = winning_pairs(hand)
        self.assertEqual(sorted(sum(pairs, [])), sorted(hand))
        with self.assertRaises(ValueError): winning_pairs(hand[:-1] + [hand[0]])

    def test_qunhu_five_triples_one_single_and_eye(self):
        hand = ['R0-1','R1-1','R2-1']
        hand += [f'{c}{r}-1' for r in (3,4,5,6) for c in ('Y','G','W')]
        hand += ['Y0-1']
        result = winning_partition(hand)
        self.assertIsNotNone(result)
        self.assertEqual(len(result['groups']), 5)
        self.assertTrue(any(g['eye'] for g in result['groups']))
        self.assertEqual(sorted(sum((g['cards'] for g in result['groups']), []) + [result['singleton']]), sorted(hand))
        self.assertIsNone(winning_partition(hand[:-1]))
        with self.assertRaises(ValueError): winning_partition(hand[:-1] + [hand[0]])

    def test_qunhu_no_eye_and_ordinary_same_color_triple_rejected(self):
        # Only same-rank, three-color triples; no 012/345 in any color.
        hand = [f'{c}{r}-1' for r in (0,1,2,3,4) for c in ('R','Y','G')] + ['W5-1']
        # 012 can reorganize into eyes, so use five separated ranks/copies instead.
        hand = [f'{c}1-{i}' for i in (1,2,3) for c in ('R','Y','G')]
        hand += [f'{c}4-{i}' for i in (1,2) for c in ('R','Y','G')] + ['W5-1']
        self.assertIsNone(winning_partition(hand))
        good = ['R3-1','R4-1','R5-1'] + [f'{c}{r}-1' for r in (1,2,6) for c in ('Y','G','W')]
        self.assertIsNotNone(winning_partition(good + ['R0-1','R0-2','R0-3','Y0-1']))
        self.assertIsNone(winning_partition(good + ['R1-1','R1-2','R1-3','Y0-1']))


if __name__ == '__main__': unittest.main()
