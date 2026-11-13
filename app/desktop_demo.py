"""Read-only guards for operator-seeded review rooms.

No public creation API, new room schema, or changes to normal game rules.
Only these IDs with the matching private board marker are frozen.
"""
DEMO_BATCH = "desktop-029-7874"
DEMO_OWNER = "7874"
DEMO_ROOMS = {
    "mahjong": "D29MJ001",
    "aeroplane_chess": "D29FL001",
    "monopoly": "D29DF001",
    "texas_holdem": "D29TX001",
    "guandan": "D29GD001",
    "uno": "D29UN001",
    "four_color_qunhu": "D29FC001",
}


# Separate human midgame fixture; never extend DEMO_ROOMS (the original batch).
KWX_DEMO_ROOM = "NSKWX001"
KWX_DEMO_OWNER = "1"
KWX_DEMO_BATCH = "nanshan-kwx-midgame-v1"


def is_kwx_review_demo(room: dict) -> bool:
    marker = room.get("board_state", {}).get("_demo")
    return (
        room.get("room_id") == KWX_DEMO_ROOM
        and room.get("game_type") == "ka_wu_xing"
        and room.get("stake") == 0
        and room.get("initiator_player_id") == KWX_DEMO_OWNER
        and isinstance(marker, dict)
        and marker.get("batch") == KWX_DEMO_BATCH
        and marker.get("owner") == KWX_DEMO_OWNER
        and any(p.get("player_id") == KWX_DEMO_OWNER
                and p.get("role") == "human" and p.get("participant_kind") == "human"
                and p.get("join_status") == "joined" and p.get("active") == True
                for p in room.get("participants", []))
    )


def is_desktop_demo(room: dict) -> bool:
    marker = room.get("board_state", {}).get("_demo")
    return is_kwx_review_demo(room) or (
        room.get("room_id") == DEMO_ROOMS.get(room.get("game_type"))
        and isinstance(marker, dict)
        and marker.get("batch") == DEMO_BATCH
        and marker.get("owner") == DEMO_OWNER
        and room.get("stake") == 0
    )


def desktop_demo_sql(prefix: str = "") -> str:
    """SQL equivalent of is_desktop_demo; prefix is an internal table alias."""
    cases = " OR ".join(
        f"({prefix}room_id = '{rid}' AND {prefix}game_type = '{game}')"
        for game, rid in DEMO_ROOMS.items()
    )
    legacy = (
        f"(({cases}) AND {prefix}stake = 0"
        f" AND COALESCE(json_extract({prefix}board_state, '$._demo.batch'), '') = '{DEMO_BATCH}'"
        f" AND COALESCE(json_extract({prefix}board_state, '$._demo.owner'), '') = '{DEMO_OWNER}')"
    )

    room_ref = prefix or "rooms."
    human = (
        f"({prefix}room_id = '{KWX_DEMO_ROOM}' AND {prefix}game_type = 'ka_wu_xing'"
        f" AND {prefix}stake = 0 AND {prefix}initiator_player_id = '{KWX_DEMO_OWNER}'"
        f" AND COALESCE(json_extract({prefix}board_state, '$._demo.batch'), '') = '{KWX_DEMO_BATCH}'"
        f" AND COALESCE(json_extract({prefix}board_state, '$._demo.owner'), '') = '{KWX_DEMO_OWNER}'"
        f" AND EXISTS (SELECT 1 FROM room_participants demo_owner"
        f" WHERE demo_owner.room_id = {room_ref}room_id"
        f" AND demo_owner.player_id = '{KWX_DEMO_OWNER}' AND demo_owner.role = 'human'"
        " AND demo_owner.participant_kind = 'human' AND demo_owner.join_status = 'joined'"
        " AND demo_owner.active = 1))"
    )
    return f"({legacy} OR {human})"
