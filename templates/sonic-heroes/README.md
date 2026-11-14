# SonicHeroesPC recompilation source

This directory is the source artifact for one user-supplied Sonic Heroes XBE.
It contains the generated C, the small Windows host, the OGlue runtime and
recompiler under `oglue/`, and build metadata. It deliberately contains no
`default.xbe`, ISO, textures, audio, video or other original game assets.

The build is experimental. This repository does not currently include the
title-specific Heroes D3D device/fence hooks; compiling is not a playability
test. See `BUILD-INFO.json` for the exact input hash, translation counts and
validation status.

## Rebuild the same executable

Requirements: Windows x64, Visual Studio 2022 with the C++ workload, CMake 3.20+.
No Python is needed for this step because the generated C is already present.

```bat
cmake -S . -B build -G "Visual Studio 17 2022" -A x64
cmake --build build --config Release --target SonicHeroesPC --parallel 2
```

Put the `game/` folder from the matching game artifact beside
`build/Release/SonicHeroesPC.exe`, or run the packaged launcher from that
artifact. The host verifies the XBE SHA-256 before executing generated code.

## Regenerate from your ISO

From this directory, with Python 3.10+:

```bat
python -m pip install -r oglue/tools/ci/requirements.txt
cd oglue
python -m tools.ci.heroes_build all --iso C:\path\to\sonic-heroes.iso --work-dir ..\regenerated --jobs 2
```

The new project is `regenerated/project`; the executable is
`regenerated/build/Release/SonicHeroesPC.exe`; the packaged game is
`regenerated/artifacts/game`. Use a fresh work directory for each run.

`SOURCE-MANIFEST.json` hashes the source files. `oglue/` keeps the upstream
MIT/LGPL notices; the generated C is derived from your own game executable and
is not covered by a blanket permission to redistribute the game's assets.
