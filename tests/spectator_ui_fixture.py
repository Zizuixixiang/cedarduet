"""Disposable database fixtures for the household spectator UI checks."""
import json
import tempfile
from pathlib import Path
from starlette.requests import Request
from app import database, framework, invites, main
from app.games import GAMES, game_catalog
from tests.test_spectator import SpectatorTests


def fixtures():
    helper = SpectatorTests()
    headers = helper.headers()
    request = Request({'type': 'http', 'headers': [(k.lower().encode(), v.encode()) for k, v in headers.items()]})
    result = {'catalog': game_catalog(), 'games': []}
    with tempfile.TemporaryDirectory(prefix='duel-spectator-ui-') as tmp:
        original = database.DB_PATH
        try:
            for game, plugin in GAMES.items():
                database.DB_PATH = Path(tmp) / f'{game}.db'
                database.init_db()
                count = max(2, min(plugin.resolved_allowed_player_counts()))
                ids = ('101', '102') + tuple(str(900 + i) for i in range(count - 2))
                room = helper.create(game, ids, start=False)
                waiting = main.spectator_response(room, request, '101')
                room = invites.start_invite(room['room_id'], 'ai', '101')
                views = [main.spectator_response(room, request, pid) for pid in ('101', '102')]
                result['games'].append({'game': game, 'waiting': waiting, 'views': views})
                if game == 'uno':
                    result['uno'] = views
                    result['rooms'] = framework.list_human_rooms('human:owner', main.spectator_machine_names(request), spectating=True)
                    room = framework.resign(room['room_id'], 'ai', '101')
                    result['terminal'] = main.spectator_response(room, request, '102')
        finally:
            database.DB_PATH = original
    return result


if __name__ == '__main__':
    print(json.dumps(fixtures(), ensure_ascii=False))
