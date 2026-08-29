"""guandan-rlcard: an open Guandan (掼蛋) environment built on RLCard.

Importing this package registers the ``'guandan'`` environment with the
RLCard registry, so both forms work:

    import guandan_rlcard
    env = guandan_rlcard.make({'seed': 42})

    import rlcard, guandan_rlcard
    env = rlcard.make('guandan')
"""

# Cedar vendors this package below ``third_party.rlcard_guandan`` instead of
# installing it as a top-level wheel.  Keep the upstream absolute imports and
# RLCard entry-point string working without mechanically rewriting every source
# file.  A normal top-level ``import guandan_rlcard`` is unchanged.
import sys

sys.modules.setdefault('guandan_rlcard', sys.modules[__name__])

from rlcard.envs.registration import registry

__version__ = '0.1.0'


def _register():
    if 'guandan' not in registry.env_specs:
        registry.register(
            env_id='guandan',
            entry_point='guandan_rlcard.envs.guandan_env:GuandanEnv',
        )


_register()

from guandan_rlcard.envs.guandan_env import GuandanEnv  # noqa: E402


def make(config=None):
    """Create a GuandanEnv with the given config dict (all keys optional)."""
    return GuandanEnv(config)
