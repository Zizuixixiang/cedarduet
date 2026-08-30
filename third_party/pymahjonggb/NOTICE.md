# PyMahjongGB

- Upstream: https://github.com/ailab-pku/PyMahjongGB
- Upstream revision: `bb404f3f3480c2569e14d54043ad06e366e128df`
- Upstream package version: `PyMahjongGB 1.4.0`
- License: MIT (see `LICENSE`); the embedded `mahjong-algorithm` sources are
  also MIT licensed (see `MahjongGB/mahjong-algorithm/LICENSE`).

## Runtime and build

The application imports the upstream native module as `MahjongGB` and calls
`MahjongFanCalculator` for every win/fan decision and `MahjongShanten` for
shanten information. `requirements.txt` installs this directory as a local
source distribution. Its unmodified upstream `setup.py` compiles
`MahjongGB/mahjong.cpp`, `fan_calculator.cpp`, and `shanten.cpp` as a C++11
CPython extension, so a C++ compiler and Python development headers are build
requirements.

Verified in this worktree with CPython 3.10.12 and GCC/G++ 11.4.0 using:

```text
cd third_party/pymahjonggb
python setup.py build_ext \
  --build-lib /tmp/pymahjonggb-runtime \
  --build-temp /tmp/pymahjonggb-build
PYTHONPATH=/tmp/pymahjonggb-runtime \
  python tests/test.py
```

## Local changes

None. The vendored upstream source, headers, Python binding, type stub,
license files, readmes, and tests are byte-for-byte copies from the revision
above. Build directories and platform-specific compiled artifacts are not
vendored; they are produced by the requirements installation step.
