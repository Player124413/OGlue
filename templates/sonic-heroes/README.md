# SonicHeroesPC — recompilation source

Static recompilation of Sonic Heroes (Original Xbox) to native Windows x64,
built on the [OGlue](https://github.com/GenryTheFox0/OGlue) runtime. The generated
C is already in `src/recomp/gen/`, so **building this repository needs no ISO,
no disc and no Python** — only Visual Studio 2022 and CMake.

This repository contains **no game assets**: no `default.xbe`, no ISO, no
textures, audio or video. The executable reads them at runtime from a `game/`
folder you supply from your own disc.

> Compiling is not a playability test. This is an experimental bring-up build.

## Layout

| Path | What it is |
|---|---|
| `src/recomp/gen/` | Generated C, one function per Xbox function |
| `src/main.c` | Windows host: window, D3D11, XAudio2, input, XBE loader |
| `src/recomp_manual.c` | Hand-written replacements for guest functions |
| `src/game_config.h` | Entry point, expected XBE SHA-256, D3D fence address |
| `oglue/` | Runtime, recompiler and tools (MIT/LGPL, see `oglue/NOTICE`) |
| `BUILD-INFO.json` | Input hash, translation counts, fence state, validation |
| `SOURCE-MANIFEST.json` | SHA-256 of every source file as generated |
| `.github/workflows/build.yml` | CI: rebuild the EXE on push, PR or manually |

## Build locally

Windows x64, Visual Studio 2022 with the **Desktop development with C++**
workload, CMake 3.20+:

```bat
cmake -S . -B build -G "Visual Studio 17 2022" -A x64
cmake --build build --config Release --target SonicHeroesPC --parallel 2
```

Output: `build\Release\SonicHeroesPC.exe`. Lower `--parallel` if the compiler
runs out of memory; the generated translation units are large.

## Build on GitHub

`.github/workflows/build.yml` compiles the committed C on `windows-2022` and
uploads `SonicHeroesPC-Windows-x64` (executable only). It runs on pushes to
`main`/`master` and on pull requests that touch source, or manually via
**Actions → Build SonicHeroesPC → Run workflow**. No secrets, no ISO, no game
data — CI never downloads or runs the game.

## Running it

Extract your own disc next to the executable so that `game\default.xbe` exists:

```bat
python -m pip install -r oglue/tools/ci/requirements.txt
cd oglue
python -m tools.xiso unpack "C:\path\to\sonic-heroes.iso" -o "<folder with the EXE>\game"
```

The host verifies the SHA-256 of `default.xbe` against `src/game_config.h` and
refuses to run a different dump — the generated code is specific to that image.
Saves go to `%LOCALAPPDATA%\xboxrecomp`.

Controls: Enter — Start, Backspace — Back, WASD — left stick, Z/X/C/V — A/B/X/Y,
Q/E — White/Black, 1/3 — triggers, arrows — D-pad, F11 or Alt+Enter — fullscreen.
XInput gamepads are picked up automatically.

## The D3D fence address

Two OGlue fixes for Heroes (honest fence, D3D busy-block wait) need one
per-title number: the guest address of the **global cell holding the pointer**
to the D3D device. It is set in `src/game_config.h` as `HEROES_D3D_DEVICE_PTR`
(`0` means the fixes stay dormant, which can show up as a hang at NOW LOADING or
flickering save cards).

Find candidates in your own XBE:

```bat
cd oglue
python -m tools.ci.find_d3d_device "..\game\default.xbe"
```

The scanner is a heuristic and never applies an address by itself. Details:
`oglue/docs/SONIC_HEROES_PC_RU.md`.

## Regenerate from an ISO

```bat
python -m pip install -r oglue/tools/ci/requirements.txt
cd oglue
python -m tools.ci.heroes_build all --iso C:\path\to\sonic-heroes.iso --work-dir ..\regenerated --jobs 2 --d3d-device-ptr 0xYOURCELL
```

Use a fresh work directory each run. The new project lands in
`regenerated/project`, the executable in `regenerated/build/Release/`.

## Publishing this on GitHub

The tree is ready to push as-is: `.gitignore` excludes `build/`, `game/`,
`*.iso`, `*.xbe`, `*.log` and IDE folders, and CI is already configured.

```bat
git init
git add .
git commit -m "Sonic Heroes recompilation"
git branch -M main
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

Two things to check before the first push:

- **Repository size.** The generated C is large. GitHub rejects files over
  100 MB and warns above 50 MB; if a chunk of `src/recomp/gen/` is that big,
  use Git LFS or split the chunk further before pushing.
- **Rights.** The generated C and the resulting EXE are derived from your copy
  of the game. OGlue's own code is MIT/LGPL, but that does not grant permission
  to redistribute Sega's code. Publishing publicly is your call; a private
  repository is the safe default. Never commit game assets — `.gitignore`
  already blocks the usual paths, but it cannot catch a renamed file.
