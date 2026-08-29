# onestraw/doudizhu rule core

- Upstream: https://github.com/onestraw/doudizhu
- PyPI: `doudizhu==0.1.5` (released 2018-04-26)
- Source distribution SHA-256: `4f844d6ac3d2271f86a0f4afbe570584f66a26ab9eaa557939b287ed82d7ba8e`
- License: MIT, Copyright (c) 2018 Larry He (see `LICENSE`)

This directory is a minimal, Python-3-only adaptation of the upstream rule
engine's rank-only enumeration and comparison model. It retains the upstream
37 fine-grained pattern names and the published 34,152 pattern-entry universe.
The implementation represents combinations as 15-rank count vectors instead
of the upstream string dictionary, and adds immutable typed results for the
host application.

Included scope: rank-only pattern enumeration, classification, comparison,
and authoritative legal rank-combination listing.

Excluded scope: upstream random dealing, integer/suit card representation,
terminal pretty-print helpers, examples, CLI, and tests. Cedar Duet owns the
physical-card mapping, room lifecycle, bidding, landlord assignment, bottom
cards, turn/pass flow, NPC integration, UI, MCP projection, and end game.
