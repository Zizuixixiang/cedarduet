"""Base class for Guandan agents.

Agents ARE players in this environment: they subclass
:class:`GuandanPlayer`, so the game engine can deal them cards and the
environment can ask them for decisions in one object. A concrete agent
implements ``step`` and may override ``tribute_act``/``back_act``.
"""

from guandan_rlcard.constants import make_card_values
from guandan_rlcard.game.player import GuandanPlayer


class GuandanAgent(GuandanPlayer):
    """Player subclass with default tribute behaviour and the agent API.

    Subclasses must implement :meth:`step`. The default
    ``tribute_act``/``back_act`` follow the rules with a simple greedy
    choice and call ``execute_tribute``/``execute_back`` themselves, as
    the protocol requires.
    """

    def __init__(self, player_id, np_random):
        super().__init__(player_id, np_random)
        self.use_raw = True

    def step(self, state):
        """Choose an action for the current state.

        Args:
            state (dict): observation; ``state['actions']`` holds the
                legal actions. When it is empty (this player already
                finished and is only queried for wind-follow turns) the
                agent must return ``[]``.

        Returns:
            list: the chosen action.
        """
        raise NotImplementedError

    def eval_step(self, state):
        """Greedy/evaluation variant of step; defaults to ``step``."""
        return self.step(state)

    def tribute_act(self, actions, level_rank):
        """Pick a tribute action, execute it and return it.

        Default: the first legal action (legal actions already encode
        the must-pay-biggest rule).
        """
        action = actions[0]
        self.execute_tribute(action)
        return action

    def back_act(self, actions, level_rank, tribute_result):
        """Pick a tribute-back action, execute it and return it.

        Default: hand back the smallest card (level-aware ordering).
        """
        values = make_card_values(level_rank)
        action = min(actions, key=lambda a: values[a[1]])
        self.execute_back(action)
        return action
