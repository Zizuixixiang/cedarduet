"""Persisted unattended-play budgets, independent of scheduler worker lifetime."""

NPC_UNATTENDED_ROUNDS = 2
# A Monopoly turn can develop 22 properties through 5 levels (110 builds),
# redeem/mortgage 28 properties, and resolve auctions, debts and card choices.
# Leave ample headroom; this is a state-machine fuse, never a gameplay rule.
MAX_NPC_ACTIONS_PER_TURN = 512


def unattended_turn_limit(room: dict) -> int:
    return NPC_UNATTENDED_ROUNDS * sum(
        p.get("join_status") == "joined"
        and p.get("active", True)
        and p.get("activity_state", "active") == "active"
        for p in room.get("participants", [])
    )


def npc_budget_exhausted(room: dict, *, max_actions: int = MAX_NPC_ACTIONS_PER_TURN) -> bool:
    return (
        room.get("npc_unattended_turns", 0) >= unattended_turn_limit(room)
        or room.get("npc_turn_actions", 0) >= max_actions
    )


def reset_npc_budget(conn, room_id: str) -> None:
    conn.execute(
        "UPDATE rooms SET npc_unattended_turns = 0, npc_turn_actions = 0 WHERE room_id = ?",
        (room_id,),
    )
