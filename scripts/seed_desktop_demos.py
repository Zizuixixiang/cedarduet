#!/usr/bin/env python3
"""Seed seven frozen, synthetic rooms; default is read-only planning.

Deploy the matching desktop_demo guards and restart the Duel service BEFORE
applying. This script never initializes/migrates a supplied DB, creates accounts,
loads credentials, calls a service/provider, posts mentions or settles stakes.
Five display NPC seats in six-player demos are seeded directly, without changing
the four-NPC admission limit for real games. Fixtures are trusted local artifacts.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import database
from app.desktop_demo import DEMO_BATCH, DEMO_OWNER, DEMO_ROOMS, is_desktop_demo
from app.framework import _decorate, project_room_for_viewer

FIXTURES = Path(__file__).with_name("desktop_demo_states.json")


def fixtures():
    payload = json.loads(FIXTURES.read_text())
    assert payload["batch"] == DEMO_BATCH and payload["owner"] == DEMO_OWNER
    rooms = payload["rooms"]
    assert {e["room"]["room_id"] for e in rooms} == set(DEMO_ROOMS.values()) and len(rooms) == 7
    for entry in rooms:
        r = entry["room"]
        assert is_desktop_demo(r) and r["status"] == "playing" and r["preserved"]
        ps = r["participants"]
        assert sum(p["participant_kind"] == "bound_machine" and p["player_id"] == DEMO_OWNER for p in ps) == 1
        assert all(p["player_id"] == DEMO_OWNER or (p["player_id"].startswith("npc:demo029_") and p["participant_kind"] == "system_npc" and p["display_name"].startswith("演示NPC")) for p in ps)
        assert all(e["text"].startswith("【合成演示】") and "@" not in e["text"] for e in entry["timeline"])
        project_room_for_viewer(_decorate(r), DEMO_OWNER)
    return rooms


def seed(db: Path, *, apply=False):
    entries = fixtures()
    # mode=rw/ro refuses typos rather than silently creating a wrong/empty DB.
    conn = sqlite3.connect(db.resolve().as_uri() + ("?mode=rw" if apply else "?mode=ro"), uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    summary = []
    try:
        conn.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
        room_columns = {r[1] for r in conn.execute("PRAGMA table_info(rooms)")}
        required = {"board_state", "preserved", "confirmation_required", "npc_unattended_turns"}
        if not required <= room_columns:
            raise ValueError("Supplied DB is not the current Duel schema; no migration performed")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for entry in entries:
            r = entry["room"]; rid = r["room_id"]
            encoded = json.dumps(r["board_state"], ensure_ascii=False, separators=(",", ":"))
            existing = conn.execute("SELECT * FROM rooms WHERE room_id=?", (rid,)).fetchone()
            if existing:
                decoded = database.decode_room(existing, conn)
                if (not is_desktop_demo(decoded) or decoded["board_state"] != r["board_state"]
                        or decoded["status"] != "playing" or not decoded["preserved"]
                        or decoded["current_player_id"] != r["current_player_id"]
                        or decoded["revision"] != r["revision"]):
                    raise ValueError(f"ID collision or changed demo {rid}; refusing to overwrite")
                keys = ("player_id", "role", "participant_kind", "npc_persona_id", "seat_index", "token", "active", "join_status")
                if [[p[k] for k in keys] for p in decoded["participants"]] != [[p[k] for k in keys] for p in r["participants"]]:
                    raise ValueError(f"Changed participants in {rid}; refusing to overwrite")
                if conn.execute("SELECT 1 FROM room_invites WHERE room_id=?", (rid,)).fetchone():
                    raise ValueError(f"Unexpected timeout/invite metadata in {rid}")
                state = "already present (unchanged)"
            else:
                # Also reserve IDs recorded by achievement history; never recycle a real ID.
                if any(conn.execute(f"SELECT 1 FROM {table} WHERE room_id=?",(rid,)).fetchone() for table in ("achievement_matches", "achievement_room_openings")):
                    raise ValueError(f"ID {rid} belongs to historical game data")
                state = "created" if apply else "would create"
                if apply:
                    conn.execute("""INSERT INTO rooms
                        (room_id,game_type,mode,board_state,turn,current_player_id,revision,status,
                         stake,initiator_player_id,confirmation_required,preserved,created_at,updated_at,last_move_at)
                        VALUES (?,?,?,?,?,?,?,'playing',0,?,0,1,?,?,?)""",
                        (rid,r["game_type"],"ai_first",encoded,"ai",r["current_player_id"],r["revision"],DEMO_OWNER,now,now,now))
                    for p in r["participants"]:
                        conn.execute("""INSERT INTO room_participants
                            (room_id,player_id,display_name,role,participant_kind,npc_persona_id,seat_index,
                             token,join_status,activity_state,active,joined_at)
                            VALUES (?,?,?,?,?,?,?,?, 'joined','active',1,?)""",
                            (rid,p["player_id"],p["display_name"],p["role"],p["participant_kind"],p["npc_persona_id"],p["seat_index"],p["token"],now))
                    for e in entry["timeline"]:
                        conn.execute("""INSERT INTO room_messages
                            (room_id,sender,sender_player_id,text,revision_at_send,created_at,event_type)
                            VALUES (?,'ai',?,?,?,?,'message')""",
                            (rid,e["sender_player_id"],e["text"],r["revision"],now))
            summary.append(dict(room_id=rid,game=r["game_type"],owner=DEMO_OWNER,seats=len(r["participants"]),state=state))
        if apply: conn.commit()
        else: conn.rollback()
    except BaseException:
        conn.rollback()
        raise
    finally: conn.close()
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, help="Explicit verified DUEL_DB_PATH; never inferred")
    parser.add_argument("--apply", action="store_true", help="Insert only the seven missing demo rooms atomically")
    parser.add_argument("--validate", action="store_true", help="Use and remove a new temporary DB; ignore production entirely")
    args = parser.parse_args()
    if args.validate:
        if args.db or args.apply: parser.error("--validate cannot be combined with --db/--apply")
        with tempfile.TemporaryDirectory(prefix="duel-desktop-demo-") as tmp:
            database.DB_PATH = Path(tmp)/"test.db"; database.init_db()
            first=seed(database.DB_PATH,apply=True);second=seed(database.DB_PATH,apply=True)
            assert all(e["state"].startswith("already present") for e in second)
            print(json.dumps(dict(validation="isolated create + idempotent rerun passed",rooms=first),ensure_ascii=False,indent=2))
    else:
        if not args.db: parser.error("--db is required (or use --validate)")
        print(json.dumps(seed(args.db,apply=args.apply),ensure_ascii=False,indent=2))

if __name__ == "__main__": main()
