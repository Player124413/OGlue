# SonicHeroesPC recompilation source

This directory is the source artifact for one user-supplied Sonic Heroes XBE.
It contains the generated C, the small Windows host, the OGlue runtime and
recompiler under `oglue/`, and build metadata. It deliberately contains no
`default.xbe`, ISO, textures, audio, video or other original game assets.

The build is experimental; compiling is not a playability test. OGlue's Heroes
fixes live in the shared runtime. Two of them (honest fence, D3D busy-block wait)
need the guest address of this title's D3D device pointer, passed at generation
time with `--d3d-device-ptr`; `src/game_config.h` shows whether it was supplied
(`HEROES_D3D_DEVICE_PTR`, 0 = no). See `BUILD-INFO.json` for the input hash,
translation counts, `d3d_fence.registered` and validation status.

## Rebuild the same executable

Requirements: Windows x64, Visual Studio 2022 with the C++ workload, CMake 3.20+.
No Python is needed for this step because the generated C is already present.

```bat
cmake -S . -B build -G "Visual Studio 17 2022" -A x64
cmake --build build --config Release --target SonicHeroesPC --parallel 2
```

Neither artifact contains game assets. Extract your own ISO into a `game/`
folder beside `build/Release/SonicHeroesPC.exe` (the packaged game artifact
carries `HOW-TO-ADD-GAME-FILES.md` with the expected hash and the exact command:
`python -m tools.xiso unpack your.iso -o game`). The host verifies the XBE
SHA-256 before executing generated code.

## Regenerate from your ISO

From this directory, with Python 3.10+:

```bat
python -m pip install -r oglue/tools/ci/requirements.txt
cd oglue
python -m tools.ci.heroes_build all --iso C:\path\to\sonic-heroes.iso --work-dir ..\regenerated --jobs 2
```

To enable the fence fixes, add the title's D3D device pointer (see
`oglue/docs/SONIC_HEROES_PC_RU.md`):

```bat
python -m tools.ci.heroes_build all --iso C:\path\to\sonic-heroes.iso --work-dir ..\regenerated2 --jobs 2 --d3d-device-ptr 0x0035FB48
```

The new project is `regenerated/project`; the executable is
`regenerated/build/Release/SonicHeroesPC.exe`; the packaged game is
`regenerated/artifacts/game`. Use a fresh work directory for each run.

`SOURCE-MANIFEST.json` hashes the source files. `oglue/` keeps the upstream
MIT/LGPL notices; the generated C is derived from your own game executable and
is not covered by a blanket permission to redistribute the game's assets.
