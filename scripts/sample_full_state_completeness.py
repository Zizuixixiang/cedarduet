"""Replay the previous full_state module on an otherwise unchanged service.

Reuse the original sampler with a fresh temporary DB for each complete run.
No persisted production state or application files are modified.
"""
import argparse
import asyncio
import importlib.util

from app import main
from sample_legacy_full_state import sample


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--before-module', help='Saved pre-fix app/full_state.py')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    if args.before_module:
        spec = importlib.util.spec_from_file_location('app._full_state_before_fix', args.before_module)
        previous = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(previous)
        main.full_state_response = previous.full_state_response
    asyncio.run(sample(args.out))
