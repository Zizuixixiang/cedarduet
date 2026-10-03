"""Output-only helpers for the four opt-in incremental MCP plugins.

These functions see viewer-safe projections, never hidden game state. Persisted
events, Web, NPC inputs and accepted action schemas are deliberately unchanged.
"""
from copy import deepcopy


def changed(current, previous):
    """Shallow replacement patch: missing keys unchanged; null is a value."""
    return {k: deepcopy(v) for k, v in current.items()
            if k not in previous or previous[k] != v}


def table_patch(melds, kinds, old_melds=(), old_kinds=()):
    return {'size': len(melds), 'set': [
        [i, deepcopy(meld), kinds[i]] for i, meld in enumerate(melds)
        if i >= len(old_melds) or meld != old_melds[i]
        or i >= len(old_kinds) or kinds[i] != old_kinds[i]]}


def compact_event(event):
    event = deepcopy(event)
    event['_mcp_merge'] = True
    if event['event_type'] in ('move', 'resign', 'leave'):
        event['_mcp_actor'] = event['sender']['player_id']
    return event
