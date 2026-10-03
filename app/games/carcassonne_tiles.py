"""Classic 72-tile base inventory, with explicit local feature connectivity.

A–X are the publisher's Big Box 2010 inventory letters, not artwork assets.
N/E/S/W = 0/1/2/3. Field ports run clockwise: NW, NE, EN, ES,
SE, SW, WS, WN = 0..7. Across an edge the two field ports reverse.
See docs/CARCASSONNE.md for source/version and verification limitations.
All geometry and code in this integration are original.
"""
from functools import lru_cache


def tile(count, cities=(), roads=(), fields=(), shields=(), monastery=False):
    regions = {}
    for prefix, kind, groups in (("c", "city", cities), ("r", "road", roads)):
        for i, ports in enumerate(groups):
            regions[f"{prefix}{i}"] = {"kind": kind, "ports": list(ports),
                                     "shields": int(kind == "city" and i in shields)}
    for i, (ports, adjacent) in enumerate(fields):
        regions[f"f{i}"] = {"kind": "field", "ports": list(ports),
                             "cities": [f"c{c}" for c in adjacent]}
    if monastery:
        regions["m0"] = {"kind": "monastery", "ports": []}
    edges = ["F"] * 4
    for r in regions.values():
        if r["kind"] in ("city", "road"):
            for side in r["ports"]:
                edges[side] = "C" if r["kind"] == "city" else "R"
    return {"count": count, "edges": edges, "regions": regions}


TILES = {
    "A": tile(2, roads=[(2,)], fields=[(range(8), [])], monastery=True),
    "B": tile(4, fields=[(range(8), [])], monastery=True),
    "C": tile(1, cities=[(0,1,2,3)], shields=[0]),
    "D": tile(4, cities=[(0,)], roads=[(1,3)], fields=[((2,7),[0]), ((3,4,5,6),[])]),
    "E": tile(5, cities=[(0,)], fields=[((2,3,4,5,6,7),[0])]),
    "F": tile(2, cities=[(1,3)], fields=[((0,1),[0]), ((4,5),[0])], shields=[0]),
    "G": tile(1, cities=[(1,3)], fields=[((0,1),[0]), ((4,5),[0])]),
    "H": tile(3, cities=[(0,), (2,)], fields=[((2,3,6,7),[0,1])]),
    "I": tile(2, cities=[(0,), (1,)], fields=[((4,5,6,7),[0,1])]),
    "J": tile(3, cities=[(0,)], roads=[(1,2)], fields=[((2,5,6,7),[0]), ((3,4),[])]),
    "K": tile(3, cities=[(0,)], roads=[(2,3)], fields=[((2,3,4,7),[0]), ((5,6),[])]),
    "L": tile(3, cities=[(0,)], roads=[(1,), (2,), (3,)], fields=[((2,7),[0]), ((3,4),[]), ((5,6),[])]),
    "M": tile(2, cities=[(0,1)], fields=[((4,5,6,7),[0])], shields=[0]),
    "N": tile(3, cities=[(0,1)], fields=[((4,5,6,7),[0])]),
    "O": tile(2, cities=[(0,1)], roads=[(2,3)], fields=[((4,7),[0]), ((5,6),[])], shields=[0]),
    "P": tile(3, cities=[(0,1)], roads=[(2,3)], fields=[((4,7),[0]), ((5,6),[])]),
    "Q": tile(1, cities=[(0,1,3)], fields=[((4,5),[0])], shields=[0]),
    "R": tile(3, cities=[(0,1,3)], fields=[((4,5),[0])]),
    "S": tile(2, cities=[(0,1,3)], roads=[(2,)], fields=[((4,),[0]), ((5,),[0])], shields=[0]),
    "T": tile(1, cities=[(0,1,3)], roads=[(2,)], fields=[((4,),[0]), ((5,),[0])]),
    "U": tile(8, roads=[(0,2)], fields=[((1,2,3,4),[]), ((0,5,6,7),[])]),
    "V": tile(9, roads=[(2,3)], fields=[((0,1,2,3,4,7),[]), ((5,6),[])]),
    "W": tile(4, roads=[(1,), (2,), (3,)], fields=[((0,1,2,7),[]), ((3,4),[]), ((5,6),[])]),
    "X": tile(1, roads=[(0,), (1,), (2,), (3,)], fields=[((1,2),[]), ((3,4),[]), ((5,6),[]), ((0,7),[])]),
}
COUNTS = {name: data["count"] for name, data in TILES.items()}
START_TILE = "D"


@lru_cache(maxsize=96)
def oriented(name, rotation=0):
    """Read-only cached topology. Rotation is clockwise quarter turns, not degrees."""
    base = TILES[name]
    edges = [None] * 4
    for i, edge in enumerate(base["edges"]):
        edges[(i + rotation) % 4] = edge
    regions = {}
    for rid, region in base["regions"].items():
        field = region["kind"] == "field"
        regions[rid] = {**region, "ports": sorted((p + rotation * (2 if field else 1)) % (8 if field else 4)
                                                 for p in region["ports"])}
    return {"tile": name, "rotation": rotation, "edges": edges, "regions": regions}


def build_deck():
    return [name for name, count in COUNTS.items() for _ in range(count - int(name == START_TILE))]
