"""Read-only guards for the seven operator-seeded desktop review rooms.

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


def is_desktop_demo(room: dict) -> bool:
    marker = room.get("board_state", {}).get("_demo")
    return (
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
    return (
        f"(({cases}) AND {prefix}stake = 0"
        f" AND COALESCE(json_extract({prefix}board_state, '$._demo.batch'), '') = '{DEMO_BATCH}'"
        f" AND COALESCE(json_extract({prefix}board_state, '$._demo.owner'), '') = '{DEMO_OWNER}')"
    )
