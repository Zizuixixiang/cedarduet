"""Self-contained full_state definitions, applied only to viewer-safe projections.

Neither raw persisted state nor bootstrap responses enter this module. Keep the
ordinary v2 protocol and its private/public baselines in mcp_minimal unchanged.
"""
from copy import deepcopy


PROTOCOL = (
    'MCP v2 recovery: replace prior context with this snapshot; no bootstrap memory required. '
    'Outer r is authoritative; submit {action:move,room_id,revision:r,move:{...}}. '
    'r and monopoly action_seq are different counters. Apply subsequent events in order, '
    'including your own; omitted delta keys mean unchanged, null clears. '
    'Covered actions must not be replayed. Unread chat/action text arrives exactly once '
    'on the next ordinary response. wait identifies the actor when you cannot act; '
    'status/participant_status and current phase govern terminal/inactive seats. '
    'private deltas affect only the viewer; snapshot.private_state is their full private state. '
    'After events, apply public_state lifecycle repairs when present. '
    'action_formats keys name move.action; no_args needs only action, and monopoly action_seq. '
    'Monopoly tile_id actions additionally require integer tile_id. All integers exclude booleans. '
)

RULES = {
    'monopoly': {
        'board': '40 indexed tiles, clockwise; tiles[].kind identifies special squares; '
                 'group is the ownership/building color set; level=0..4 houses, 5 hotel. '
                 'tiles[].rents[level], build_cost, mortgage_value and redemption_cost are authoritative.',
        'money': {'initial_cash': 1500, 'start_salary': 200, 'income_tax': 200,
                  'luxury_tax': 100, 'bail': 50, 'bank_houses': 32, 'bank_hotels': 12},
        'turn': 'turn_player_id owns the current decision; current_player_id owns the overall turn. '
                'roll: roll or manage assets; purchase: buy landed tile or auction; '
                'manage: manage then roll if extra_roll, otherwise end_turn. Doubles grant another roll; '
                'third consecutive doubles jail you. Server rolls/lands/charges automatically. '
                'Passing or landing on start pays salary; jail/backward moves do not.',
        'rent': 'Mortgaged tiles pay zero; jailed owners still collect. Unbuilt complete property set '
                'doubles base rent; buildings use rents[level]. Railroads=25/50/100/200 for 1/2/3/4 owned; '
                'utilities=4*dice sum, 10*sum if both owned. Card railroad rent doubles; '
                'card utility rent uses a fresh roll times 10.',
        'assets': 'Asset actions allowed in roll/purchase/manage/debt. Build one level at a time only '
                  'on a complete unmortgaged property set, raising a lowest level; max 5, pay build_cost. '
                  'Sell a highest level for half build_cost; hotel downgrade needs 4 bank houses. '
                  'Bank supply limits building; bankruptcy liquidation ignores supply. '
                  'Mortgage/trade only if the entire group has no buildings. Mortgage pays mortgage_value; '
                  'redeem costs redemption_cost (ceil(mortgage principal*1.10)). No build/redeem in debt.',
        'auction': 'All solvent players including the decliner bid in order. bid amount is integer '
                   '>auction.bid and <=bidder cash, or pass_bid. Highest bidder pays automatically; '
                   'no bidder leaves title with bank. auction is the complete current window.',
        'trade': 'At most 3 proposals per overall turn, in roll/purchase/manage/debt. Proposer consents '
                 'by submitting; only the recipient responds accept true/false at the start of their later normal turn, '
                 'before other actions. A pending offer does not interrupt the proposer or other players; '
                 'trades holds all pending offers, at most one per recipient. trade is a legacy alias: '
                 'actor incoming offer, else oldest. Use snapshot.legal_actions for the current response gate. '
                 'Invalidated cash, ownership, mortgage status or bankruptcy cancels the offer. '
                 'Legacy phase=trade saves still require an immediate recipient response. Cash must be nonnegative integer '
                 'within each balance; tiles unique and owned by giver, entire group unbuilt. '
                 'Nonempty exchange required. Revalidated atomically on acceptance. Mortgages transfer '
                 'without extra tax; jail cards cannot trade. trade_options.partners is viewer-specific.',
        'jail': 'At roll pay_bail=50 or use_jail_card, then roll normally; alternatively roll for doubles. '
                'Jail doubles move but grant no extra roll. Third failed roll requires 50 before moving '
                'that dice sum, allowing debt fundraising/bankruptcy. Parking pays nothing.',
        'cards': 'chance=opportunity, chest=community; each has 16 cycling shuffled cards, order hidden. '
                 'Effects: cash, payments between players, movement, repairs, jail and retained jail cards. '
                 'Repairs: chance 25/house+100/hotel, chest 40/house+115/hotel. '
                 'last_card_events preserves public drawn deck/text/summary and event/action IDs, '
                 'possibly multiple cards per move. jail_cards is your count; used/bankrupt-held cards '
                 'return to deck bottom. Never infer the next card.',
        'debt': 'debt gives payer/creditor/amount/reason. Fund via selling, mortgaging or trading; '
                'sufficient cash clears the queued charge exactly once automatically. bankrupt forfeits: '
                'liquidate buildings at half cost; player creditor gets residual cash/titles with mortgages; '
                'bank creditor clears mortgages and auctions titles. Unpaid balance forgiven. '
                'Resignation follows current creditor, otherwise bank. Last solvent player wins; '
                'no fixed turn limit; local cash never changes platform chips.',
    },
    'rummikub': {
        'inventory': {'colors': ['red', 'blue', 'black', 'orange'], 'numbers': [1, 13],
                      'copies': 2, 'jokers': 2, 'tiles': 106, 'starting_hand': 14},
        'group': '3..4 tiles of one number, distinct colors; copies are distinct physical IDs, not extra colors.',
        'run': '3..13 consecutive tiles of one color, ascending; 1 is lowest, 13 never wraps to 1.',
        'opening_threshold': 30,
        'opening': 'Use only original own hand for new groups totaling >=30; joker scores its represented '
                   'number. All old melds remain unchanged: no extending, borrowing or rearranging them.',
        'rearrange': 'After opening, split/merge/reorder freely; every old table ID remains exactly once '
                     'and at least one original hand ID is added. No duplicate IDs or taking table tiles back.',
        'joker': 'joker-1/2 substitute a number/color; run position fixes number. joker_roles[joker_id]={number,colors} '
                 'lists allowed roles, not independent simultaneous choices: jokers in one group have distinct '
                 'colors. An old joker without any original hand tile in its new meld must retain a jointly '
                 'consistent old kind/number/color and at least one old non-joker companion. Otherwise it is '
                 'released and must be reused this turn in a meld containing an original hand tile; never '
                 'return to hand. Unopened players cannot release table jokers. All-joker melds are invalid.',
        'turn': 'draw one tile and end turn; cannot play it until next turn. pass only with empty pool; '
                'all active players consecutively pass to finish, any meld/draw/exit clears blocked. '
                'Pass is a player declaration, not exhaustive proof. Invalid atomic submission changes nothing.',
        'scoring': 'First empty hand wins; others lose remaining number values (joker=30), winner gains total. '
                   'Blocked: lowest remaining value wins, others lose difference to minimum; tied winners '
                   'share positive total, fractions allowed. Resigned hands stay sealed, not recycled; '
                   'last active wins; games with resignation have no tile scores. No platform chip stakes.',
    },
    'bomb_plane': {
        'coordinates': 'A..J west to east, 1..10 north to south; x/y offsets increase east/south.',
        'north_offsets': [[0, 0], [-2, 1], [-1, 1], [0, 1], [1, 1], [2, 1],
                          [0, 2], [-1, 3], [0, 3], [1, 3]],
        'directions': 'N/E/S/W point the head north/east/south/west. Offsets are relative to head for N; '
                      'each clockwise quarter turn maps (dx,dy) to (-dy,dx). All 10 cells must be in bounds.',
        'layout': 'Exactly 3 planes to ready. Bodies may overlap; heads must be distinct. '
                  'A head may overlap another body. Setup is simultaneous/private; each unlocked viewer '
                  'may edit without waiting. ready locks own layout; both ready starts alternating attacks.',
        'shots': 'shots[attacker][miss|hit|head]=all attacked cell IDs of that result, targeting the other '
                 'player. head if ANY head occupies cell, else hit if ANY body, else miss. '
                 'All outcomes switch turn. Never attack a previously attacked cell. First 3 heads wins. '
                 'A head hit reveals only that cell, not a plane; downed bodies still return hit. '
                 'Own planes remain private; only terminal revealed_planes exposes both layouts, including resignation.',
    },
    'carcassonne': {
        'edition': 'Classic 72 tiles including start D, 7 meeples/player; roads, cities, monasteries and farmers; '
                   'no expansions or garden effects. topology A-X describes public types, never deck order.',
        'topology': 'edges in N/E/S/W order: C=city,R=road,F=field. Each regions entry is internally '
                    'connected; different entries are separate. c/r/f/m IDs identify city/road/field/monastery. '
                    'ports connect to matching neighboring features; shields count city bonuses; '
                    'field cities lists adjacent local city IDs. City edges have no field ports. '
                    'meeple.region refers to that tile type region, unchanged by rotation.',
        'placement': 'Place current_tile at empty integer x,y, edge-adjacent to >=1 tile, all touching '
                     'edges matching; corners alone do not count. Server draws; never submit tile or deck. '
                     'A globally unplaceable draw is publicly discarded and replaced without skipping player.',
        'meeple': 'Optional one from supply on newly placed tile, only if the whole connected feature '
                  'contains NO player meeple. Placement precedes scoring: a meeple returned this turn '
                  'cannot be used retroactively. Later placements may join occupied features.',
        'scoring': {'completed_road_per_tile': 1, 'completed_city_per_tile_or_shield': 2,
                    'unfinished_road_per_tile': 1, 'unfinished_city_per_tile_or_shield': 1,
                    'monastery_max': 9, 'field_per_adjacent_completed_city': 3},
        'completion': 'Road junctions end separate roads; closed loops complete. Count each tile once '
                      'within a connected road/city even if its local regions join outside it. Monastery '
                      'scores itself+occupied 8 neighbors, completing at 9. Completed roads/cities/monasteries '
                      'score after placement and return ALL their meeples. Majority gets full score; '
                      'tied majority each gets full score.',
        'end': 'Farmers stay until final scoring. Fields separated by roads/cities score each distinct '
               'adjacent completed city once; separate fields may each score it, using majority/tie rule. '
               'At deck exhaustion score incomplete roads/cities/monasteries and fields, returning meeples. '
               'Highest active score wins, ties allowed. Exit removes own meeples to supply and disqualifies '
               'the seat; last active wins, final scoring still runs. Unique winner receives each loser stake; '
               'tied winners refund stakes; board scores are separate from chips.',
        'query': 'Current actor may repeat state(move={query:placements,all:true}), or '
                 '{query:placements,x,y,rotation?,meeple?} for validity and <=8 nearby choices. '
                 'Rows=[x,y,rotation,[available region IDs]], null meeple always permitted on a legal placement. '
                 'Query does not consume events; check its revision against outer r. No wait/message/full_state '
                 'on query. It replaces only the legal-placement list, not topology.',
    },
}

ACTION_FORMATS = {
    'monopoly': {
        'all': 'Every move has action and current board_state.action_seq. Only turn_player_id acts. '
               'legal_actions are viewer-specific examples at this revision, not a complete bid range.',
        'no_args': ['roll', 'buy', 'auction', 'end_turn', 'pass_bid', 'pay_bail', 'use_jail_card', 'bankrupt'],
        'tile_id': ['build', 'sell_building', 'mortgage', 'redeem'],
        'bid': {'amount': 'integer > auction.bid and <= own cash'},
        'respond_trade': {'accept': 'boolean'},
        'propose_trade': {'to': 'partner player_id', 'give_cash': 'nonnegative integer',
                          'take_cash': 'nonnegative integer', 'give_tiles': 'tile ID array', 'take_tiles': 'tile ID array'},
    },
    'rummikub': {
        'meld': {'melds': 'FULL final table: 1..35 arrays, 3..13 physical tile IDs each; runs ascending, '
                          'joker at substituted position; include every old tile exactly once',
                 'kinds': 'optional parallel array of group|run; omitted chooses highest-points valid '
                          'interpretation; preserve old meld_kinds when retaining old groups'},
        'no_args': ['draw', 'pass'],
    },
    'bomb_plane': {
        'place': {'head': 'A1..J10', 'direction': ['N', 'E', 'S', 'W']},
        'set_layout': {'planes': '0..3 objects {head:A1..J10,direction:N|E|S|W}; replaces own layout'},
        'no_args': {'undo': 'remove last plane, requires nonempty layout', 'clear': 'empty layout',
                    'shuffle': 'random 3 planes, NOT ready', 'ready': 'lock exactly 3 planes',
                    'auto_setup': 'random 3 planes AND ready'},
        'attack': {'cell': 'A1..J10 not in own shots; only in play phase by active_player_id'},
    },
    'carcassonne': {
        'place': {'x': 'integer', 'y': 'integer', 'rotation': [0, 1, 2, 3],
                  'meeple': 'null or available region ID of current_tile; all four fields required'},
    },
}


def recovery_snapshot(value, pid, event_format):
    """value MUST be project_mcp_snapshot_for_viewer output, never raw state."""
    out = deepcopy(value)
    name = out['game']
    # The plugin's legacy delta format differs from v2. Replace its explanation,
    # not the wire events or any dynamic state, with the existing v2 encoding.
    out['board_state'].pop('delta_format', None)
    out.update(viewer_player_id=pid, protocol=2, rules=deepcopy(RULES[name]),
               action_formats=deepcopy(ACTION_FORMATS[name]),
               protocol_guide=PROTOCOL + event_format.replace(
                   'Static tiles/rents are in bootstrap.', 'Static tiles/rents are in this snapshot.'))
    private = out['private_state']
    if name == 'monopoly':
        out['legal_actions'] = private['legal_actions']
        out['trade_options'] = private.get('trade_options', {'partners': []})
    keys = {'monopoly': ('jail_cards',), 'rummikub': ('hand',),
            'bomb_plane': ('planes',), 'carcassonne': ()}[name]
    out['private_state'] = {k: private[k] for k in keys}
    return out
