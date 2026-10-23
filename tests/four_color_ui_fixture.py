"""Disposable subprocess transport for the actual Duel page/browser test."""

import json
import os
import random
import sys
from unittest.mock import patch

if not os.environ.get("DUEL_DB_PATH"):
    raise SystemExit("Explicit disposable DUEL_DB_PATH required")
from app import database, framework, invites
from app.games import GAMES
from app.games.four_color_cards.tenhu import BgaTenHu
from app.games.four_color_cards.jiuzhi import Jiuzhi
from app.games.four_color_cards.qunhu import Qunhu
from app.games.four_color_cards.qunhu_rules import waiting_codes, exhibit
from tests.test_four_color_variants import (
    custom,
    offer as variant_offer,
    physical,
    ready,
)
from tests.test_four_color_tenhu import players, fixture, offer, copies, physical_cards


def seed(request):
    database.init_db()
    count = request.get("count", 4)
    game_type = request.get("game_type", "four_color_tenhu")
    if request.get("invited"):
        room = invites.create_invite(
            game_type, "human", "p0", target_player_count=count
        )
        for i in range(1, count):
            invites.join_invite(room["invite_code"], "human", f"p{i}")
        if request.get("fixture") == "waiting":
            return framework.get_room(room["room_id"])
        with patch.object(invites.secrets, "SystemRandom") as rng:
            rng.return_value.shuffle.side_effect = lambda seats: None
            room = invites.start_invite(room["room_id"], "human", "p0")
    else:
        room = framework.create_room(
            game_type, "human_first", "human", "p0", ordered_participants=players(count)
        )
    kind = request.get("fixture", "opening")
    if kind == "dense":
        # Legal replay only: accept offers at different rates per seat and
        # discard a legal own card. Declining optional wins keeps the hand deep.
        # Never inject cards, sources or a replacement board into this room.
        game = GAMES[game_type]
        policy_rng = random.Random(request.get("seed", 9) + 20261023)
        moves = []
        for _ in range(1200):
            actor = next(p for p in room["participants"]
                         if p["player_id"] == room["current_player_id"])
            state = room["board_state"]
            legal = game.private_state(state, actor, room["participants"])["legal_actions"]
            by_id = {m["action_id"]: m for m in legal}
            discards = [m for m in legal if m["action_id"].startswith("discard:")]
            seat = state["participant_order"].index(actor["player_id"])
            if discards:
                move = policy_rng.choice(discards)
            elif "take" in by_id and policy_rng.random() < (.9, .25, .65, .1, .45)[seat]:
                move = by_id["take"]
            else:
                move = by_id.get("pass") or by_id.get("draw") or next(
                    (m for m in legal if not m["action_id"].startswith(("hu", "listen"))), legal[0])
            assert move in legal
            next_state = game.apply_action(state, move, actor).state
            if next_state.get("result"):
                break  # Leave the deepest still-playing state for the UI.
            room = framework.play_move(room["room_id"], actor["role"], actor["player_id"], move,
                                       expected_revision=room["revision"])
            moves.append({"player_id": actor["player_id"], "move": move})
            cards = physical_cards(room["board_state"]) if game_type == "four_color_tenhu" else physical(room["board_state"])
            assert len(cards) == len(set(cards)) == 112
        else:
            raise AssertionError("Dense replay did not reach the end of the wall")
        request["replay"] = {"seed": request.get("seed", 9), "policy": "uneven-take-v1",
                             "game_type": game_type, "count": count, "moves": moves,
                             "discards": len(room["board_state"]["discards"]),
                             "player_discards": {p: sum(room["board_state"].get("discard_sources", {}).get(c) == p
                                                       for c in room["board_state"]["discards"])
                                                 for p in room["board_state"]["participant_order"]}}
    if kind == "midgame":
        # Advance only this disposable room with the engine's legal actions.
        # Screenshots of normal play must not be synthetic meld arrangements.
        game = GAMES[game_type]
        for _ in range(64):
            actor = next(p for p in room["participants"]
                         if p["player_id"] == room["current_player_id"])
            move = game.choose_local_npc_action(room["board_state"], actor, room["participants"])
            if game.apply_action(room["board_state"], move, actor).state.get("result"):
                break
            room = framework.play_move(room["room_id"], actor["role"], actor["player_id"], move,
                                       expected_revision=room["revision"])
            if room["revision"] >= 32 and room["board_state"]["pending"]:
                break
        assert room["revision"] >= 8, "Midgame fixture needs several real exchanges"
    if kind in ("hu", "empty", "listen"):
        if game_type != "four_color_tenhu":
            game = GAMES[game_type]
            other = [[f"Y5-{i}"] for i in range(1, count)]
            if kind == "empty":
                state = custom(game, [["R1-1"]] + other, wall=[])
                state["flow"]["phase"] = "draw"
            elif game_type == "four_color_jiuzhi":
                state = custom(
                    game,
                    [["R6-1"]] + other,
                    {"p0": [copies(c, 2) for c in ("R0", "G1", "W2", "Y3")]},
                )
                variant_offer(game, state, "R6-4")
            else:
                other = [[f"G5-{i}"] for i in range(1, count)]
                state = custom(
                    game, [ready() + (["R4-4"] if kind == "listen" else [])] + other
                )
                if kind == "hu":
                    state["listening"]["p0"] = {
                        "waits": waiting_codes(ready()),
                        "exhibit": exhibit(ready()),
                    }
                    variant_offer(game, state, "G6-4")
            assert len(physical(state)) == len(set(physical(state))) == 112
        elif kind == "hu":
            hand = (
                copies("R1", 3)
                + copies("Y1", 3)
                + copies("G1", 3)
                + ["W0-1", "R3-1", "R4-1"]
            )
            game, state = fixture([hand] + [[f"Y5-{i}"] for i in range(1, count)])
            offer(game, state, "R5-4", from_deck=True)
        else:
            game, state = fixture(
                [["R1-1"]] + [[f"Y5-{i}"] for i in range(1, count)], wall=[]
            )
            state["flow"]["phase"] = "draw"
        if game_type == "four_color_tenhu":
            assert len(physical_cards(state)) == len(set(physical_cards(state))) == 112
        with database.write_transaction() as conn:
            conn.execute(
                "UPDATE rooms SET board_state=?,current_player_id=? WHERE room_id=?",
                (json.dumps(state), state["turn_player_id"], room["room_id"]),
            )
        room = framework.get_room(room["room_id"])
    return room


if __name__ == "__main__":
    request = json.load(sys.stdin)
    with patch.dict(
        GAMES, {g.game_type: g(random.Random(request.get("seed", 9))) for g in (BgaTenHu, Jiuzhi, Qunhu)}
    ):
        try:
            if request["action"] == "seed":
                room = seed(request)
            elif request["action"] == "move":
                room = framework.play_move(
                    request["room_id"],
                    "human",
                    "p0",
                    request["move"],
                    expected_revision=request["revision"],
                )
            elif request["action"] == "start":
                room = invites.start_invite(request["room_id"], "human", "p0")
            elif request["action"] == "state":
                room = framework.get_room(request["room_id"])
            else:
                raise ValueError("Unknown fixture command")
            result = {
                "ok": True,
                "room": framework.project_room_for_viewer(room, "p0"),
                "timeline": [],
                "message": "",
            }
            if "replay" in request:
                result["replay"] = request["replay"]
        except framework.DuelError as exc:
            result = {"ok": False, "message": str(exc), "status": exc.status_code}
        print(json.dumps(result, ensure_ascii=False))
