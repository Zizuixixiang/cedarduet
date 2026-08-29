"""End-to-end environment flow with random agents."""

import numpy as np

import guandan_rlcard
from guandan_rlcard.baselines.random_agent import RandomAgent

STEP_CAP = 6000


def make_env(seed=42, perfect_info=True):
    env = guandan_rlcard.make({'seed': seed, 'perfect_info': perfect_info})
    agents = [RandomAgent(i, np.random.RandomState(seed + i))
              for i in range(4)]
    env.set_agents(agents)
    return env


def drive(env, stop):
    """Step the env until ``stop(env, steps)`` or the episode ends.

    Returns:
        list: the (player_id, action) pairs taken.
    """
    state, player_id = env.reset()
    taken = []
    steps = 0
    while not env.is_over() and not stop(env, steps):
        action = env.agents[player_id].step(state)
        taken.append((player_id, action))
        state, player_id = env.step(action)
        steps += 1
    return taken


def test_one_deal_completes_and_levels_update():
    env = make_env(seed=7)
    drive(env, stop=lambda e, steps: e.game.game_count >= 2
          or steps > STEP_CAP)
    # The second deal started: a team gained levels and tribute ran.
    assert env.game.game_count >= 2
    assert sum(env.game.gwin) == env.game.game_count - 1
    assert max(env.game.team0_rank, env.game.team1_rank) >= 1
    # All 108 cards are back in play.
    hand_sizes = [len(p.current_hand) for p in env.game.players]
    assert sum(hand_sizes) == 108


def test_states_carry_legal_actions_and_oracle():
    env = make_env(seed=11)
    state, player_id = env.reset()
    assert state['actions'], 'the leader must have legal actions'
    assert 'current_oracle' in state
    assert state['self'] == player_id
    assert 'all_players_hands' in state  # perfect info on by default
    action = env.agents[player_id].step(state)
    next_state, _ = env.step(action)
    assert 'actions' in next_state


def test_perfect_info_can_be_disabled():
    env = make_env(seed=11, perfect_info=False)
    state, _ = env.reset()
    assert 'all_players_hands' not in state
    assert 'min_steps_estimation' not in state
    assert 'current_hand' in state


def test_same_seed_same_trajectory():
    runs = []
    for _ in range(2):
        env = make_env(seed=123)
        taken = drive(env, stop=lambda e, steps: steps >= 400)
        runs.append(taken)
    assert runs[0] == runs[1]


def test_rlcard_make_integration():
    import rlcard
    env = rlcard.make('guandan')
    assert env.name == 'guandan'
    agents = [RandomAgent(i, np.random.RandomState(i)) for i in range(4)]
    env.set_agents(agents)
    state, player_id = env.reset()
    assert 0 <= player_id < 4
    assert state['actions']


def test_payoffs_zero_while_running():
    env = make_env(seed=5)
    env.reset()
    assert env.get_payoffs() == [0.0, 0.0, 0.0, 0.0]
