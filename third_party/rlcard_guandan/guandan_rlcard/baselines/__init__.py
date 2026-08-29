"""Minimal vendored baseline surface needed by the upstream flow tests.

Cedar intentionally does not vendor the upstream research/training agents,
model weights, or LLM integration.  The dependency-free random baseline is
retained because ``tests/test_game_flow.py`` exercises the native environment
with it.
"""

from guandan_rlcard.baselines.random_agent import RandomAgent

AGENT_REGISTRY = {
    'random': ('guandan_rlcard.baselines.random_agent', 'RandomAgent'),
}


def get_agent_class(name):
    """Resolve a baseline name (see AGENT_REGISTRY) to its agent class."""
    if name not in AGENT_REGISTRY:
        raise KeyError(f'Unknown baseline {name!r}; choose from '
                       f'{sorted(AGENT_REGISTRY)}')
    return RandomAgent


__all__ = ['RandomAgent', 'AGENT_REGISTRY', 'get_agent_class']
