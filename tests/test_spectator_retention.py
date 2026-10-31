"""Retention is the only write granted by current household spectating rights."""
import base64
import json
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import httpx

from app import achievements, database, framework, invites, main


class SpectatorRetentionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="duel-spectator-retention-")
        self.addCleanup(temporary.cleanup)
        db_patch = patch.object(database, "DB_PATH", Path(temporary.name) / "test.db")
        db_patch.start()
        self.addCleanup(db_patch.stop)
        database.init_db()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test"
        )
        self.addAsyncCleanup(self.client.aclose)

    def headers(self, ids=("101",), human="human:owner"):
        encoded = base64.urlsafe_b64encode(json.dumps([
            {"id": pid, "name": f"小机{pid}"} for pid in ids
        ]).encode()).decode()
        return {"X-Duel-Human-Player": human, "X-Duel-Bound-Ais": encoded}

    def create(self, ids=("101", "999"), status="finished"):
        room = invites.create_invite(
            "uno", "ai", ids[0], target_player_count=len(ids)
        )
        for pid in ids[1:]:
            invites.join_invite(
                room["invite_code"], "human" if pid.startswith("human:") else "ai", pid
            )
        if status != "waiting":
            room = invites.start_invite(room["room_id"], "ai", ids[0])
        if status in {"finished", "archived"}:
            for pid in ids[:-1]:
                if room["status"] in {"finished", "archived"}:
                    break
                room = framework.resign(
                    room["room_id"], "human" if pid.startswith("human:") else "ai", pid
                )
        if status == "archived":
            with database.write_transaction() as conn:
                conn.execute("UPDATE rooms SET status='archived' WHERE room_id=?", (room["room_id"],))
        return room

    async def retain(self, room, preserved=True, headers=None, perspective=None, **extra):
        return await self.client.post(
            f"/api/rooms/{room['room_id']}/retention",
            headers=self.headers() if headers is None else headers,
            params={} if perspective is None else {"perspective": perspective},
            json={"preserved": preserved, **extra},
        )

    def dump(self):
        with closing(database.connect()) as conn:
            return "\n".join(conn.iterdump())

    async def test_account_and_slots_preserve_and_unpreserve_terminal_rooms(self):
        for status in ("finished", "archived"):
            for pid in ("101", "101:2", "101:3", "101:4", "101:5"):
                with self.subTest(status=status, pid=pid):
                    room = self.create((pid, "999"), status)
                    terminal_at = datetime.fromisoformat(room["terminal_at"])
                    for preserved in (True, False):
                        response = await self.retain(room, preserved)
                        self.assertEqual(response.status_code, 200, response.text)
                        actual = response.json()["room"]
                        self.assertEqual(actual["status"], status)
                        self.assertTrue(actual["spectating"])
                        self.assertEqual(actual["viewer"]["player_id"], pid)
                        self.assertEqual(actual["preserved"], preserved)
                        expected_deadline = None if preserved else (
                            terminal_at + timedelta(days=7)
                        ).isoformat(timespec="seconds")
                        self.assertEqual(actual["auto_delete_at"], expected_deadline)
                        listed = await self.client.get("/api/spectating/rooms", headers=self.headers())
                        summary = next(r for r in listed.json()["rooms"] if r["room_id"] == room["room_id"])
                        self.assertTrue(summary["spectating"])
                        self.assertEqual(summary["preserved"], preserved)
                        self.assertEqual(summary["auto_delete_at"], expected_deadline)

    async def test_original_human_keeps_view_and_achievement_path(self):
        room = self.create(("101", "human:owner"))
        with patch.object(achievements, "record_preserved_loss", wraps=achievements.record_preserved_loss) as record:
            response = await self.retain(room, headers=self.headers(()), perspective="999")
            self.assertEqual(response.status_code, 200, response.text)
            record.assert_called_once()
            self.assertEqual(record.call_args.args[1:], ("human:owner", room["room_id"]))
        actual = response.json()["room"]
        self.assertNotIn("spectating", actual)
        self.assertEqual(actual["viewer"]["role"], "human")
        self.assertEqual(actual["viewer"]["player_id"], "human:owner")
        self.assertEqual((await self.retain(room, False, headers=self.headers(()))).status_code, 200)

    async def test_missing_foreign_and_forged_bindings_are_rejected(self):
        room = self.create()
        cases = [
            ({}, {}, 403), (self.headers(()), {}, 403), (self.headers(("888",)), {}, 403),
            (self.headers(("101:2",)), {}, 403),
            (self.headers(()), {"player_id": "101"}, 403),
            (self.headers(()), {"player_id": "101", "ai_player_id": "101", "bound_ais": [{"id": "101"}]}, 422),
            ({**self.headers(), "X-Duel-Bound-Ais": "malformed"}, {}, 403),
        ]
        for headers, extra, expected_status in cases:
            for preserved in (True, False):
                with self.subTest(headers=headers, preserved=preserved):
                    before = self.dump()
                    response = await self.retain(room, preserved, headers=headers, **extra)
                    self.assertEqual(response.status_code, expected_status, response.text)
                    self.assertEqual(self.dump(), before)
        for pid in ("101:1", "101:6"):
            invalid_slot = self.create((pid, "999"))
            before = self.dump()
            self.assertEqual((await self.retain(invalid_slot)).status_code, 403)
            self.assertEqual(self.dump(), before)

    async def test_revocation_npc_and_departed_seats_cannot_toggle(self):
        room = self.create()
        self.assertEqual((await self.retain(room)).status_code, 200)
        before = self.dump()
        for preserved in (True, False):
            self.assertEqual((await self.retain(room, preserved, headers=self.headers(()))).status_code, 403)
        self.assertEqual(self.dump(), before)
        for change in ("participant_kind='system_npc'", "join_status='left'"):
            room = self.create()
            with database.write_transaction() as conn:
                conn.execute(f"UPDATE room_participants SET {change} WHERE room_id=? AND player_id='101'", (room["room_id"],))
            before = self.dump()
            for preserved in (True, False):
                self.assertEqual((await self.retain(room, preserved)).status_code, 403)
            self.assertEqual(self.dump(), before)
        # Like GET, a departed human cannot fall back to their bound AI's seat.
        room = self.create(("101", "human:owner"))
        with database.write_transaction() as conn:
            conn.execute("UPDATE room_participants SET join_status='left' WHERE room_id=? AND role='human'", (room["room_id"],))
        before = self.dump()
        self.assertEqual((await self.retain(room)).status_code, 403)
        self.assertEqual(self.dump(), before)

    async def test_waiting_playing_and_stale_playing_cannot_be_preserved(self):
        for status in ("waiting", "playing", "stale"):
            room = self.create(status="playing" if status == "stale" else status)
            if status == "stale":
                with database.write_transaction() as conn:
                    conn.execute("UPDATE rooms SET last_move_at='2020-01-01T00:00:00+00:00' WHERE room_id=?", (room["room_id"],))
            before = self.dump()
            for preserved in (True, False):
                response = await self.retain(room, preserved)
                self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(self.dump(), before)

    async def test_response_keeps_selected_projection_and_only_changes_preserved(self):
        room = self.create(("101", "102", "999"))
        with database.write_transaction() as conn:
            for pid in ("101", "102", "999"):
                conn.execute("""INSERT INTO room_messages
                    (room_id,sender,sender_player_id,text,revision_at_send,created_at,visible_to_json)
                    VALUES (?,?,?,?,?,?,?)""", (room["room_id"], "ai", pid, "private-" + pid,
                    room["revision"], framework._now(), json.dumps([pid])))
        headers = self.headers(("101", "102"))
        before = self.dump()
        baseline = (await self.client.get(f"/api/rooms/{room['room_id']}", headers=headers,
                                         params={"perspective": "102"})).json()
        with patch.object(achievements, "record_preserved_loss", side_effect=AssertionError("spectator achievement")), \
             patch.object(main, "human_response", side_effect=AssertionError("human projection")), \
             patch.object(main, "_schedule_current_system_npc", side_effect=AssertionError("NPC scheduling")):
            for preserved in (True, False):
                response = await self.retain(room, preserved, headers=headers, perspective="102")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.headers["cache-control"], "private, no-store")
                actual = response.json()
                self.assertNotIn("unlocks", actual)
                expected_room = {**baseline["room"], "preserved": preserved,
                                 "auto_delete_at": None if preserved else baseline["room"]["auto_delete_at"]}
                self.assertEqual(actual["room"], expected_room)
                self.assertEqual(actual["timeline"], baseline["timeline"])
                self.assertEqual([e["text"] for e in actual["timeline"] if e.get("text", "").startswith("private-")], ["private-102"])
                self.assertEqual(actual["poll_after_ms"], baseline["poll_after_ms"])
        # Round trip changes no achievements, notifications, cursors or presence.
        self.assertEqual(self.dump(), before)
        for perspective in ("999", "101:6"):
            response = await self.retain(room, headers=headers, perspective=perspective)
            self.assertEqual(response.status_code, 403, response.text)
            self.assertEqual(self.dump(), before)

    async def test_other_writes_still_rejected_after_spectator_preserves(self):
        room = self.create(("101", "human:other"))
        self.assertEqual((await self.retain(room)).status_code, 200)
        before = self.dump()
        for player_id in ("human:owner", "human:other", "101"):
            for action, extra in (
                ("move", {"move": {"action": "draw"}, "revision": room["revision"]}),
                ("messages", {"message": "forged"}), ("resign", {}), ("delete", {}),
            ):
                response = await self.client.post(
                    f"/api/rooms/{room['room_id']}/{action}", headers=self.headers(),
                    json={"player_id": player_id, **extra},
                )
                self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self.dump(), before)
