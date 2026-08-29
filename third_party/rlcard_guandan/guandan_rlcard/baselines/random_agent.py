"""Random baseline: uniformly picks one of the legal actions.

Useful as the weakest reference opponent and for smoke-testing the
environment. Tribute decisions use the rule-following defaults from
:class:`GuandanAgent` (pay the forced biggest card, hand back the
smallest legal card).
"""

from guandan_rlcard.agents.base_agent import GuandanAgent


class RandomAgent(GuandanAgent):
    """Uniform random agent over the legal action list."""

    def step(self, state):
        actions = state['actions']
        if not actions:
            # Finished hand being queried for a wind-follow turn.
            return []
        index = self.np_random.randint(len(actions))
        return actions[index]
