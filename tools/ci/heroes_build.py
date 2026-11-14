#!/usr/bin/env python3
"""Sonic Heroes: user ISO -> OGlue C -> Windows x64 executable -> two artifacts.

This automates the pipeline present in this repository. It does not invent the
missing title-specific D3D device/fence hooks or claim that a successful link is
a playability test. The generated project and the playable-data archive are kept
separate; neither is written back to Git.

    python -m tools.ci.heroes_build prepare --iso game.iso --work-dir .work/heroes
    python -m tools.ci.heroes_build build --work-dir .work/heroes --jobs 2
    python -m tools.ci.heroes_build all --iso game.iso --work-dir .work/heroes
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from collections import deque
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
TEMPLATE = ROOT / "templates" / "sonic-heroes"
# Player-facing extras live apart from the source template so the source
# artifact stays a clean, publishable repository (no launcher, no run README).
PACKAGE_TEMPLATE = ROOT / "templates" / "sonic-heroes-package"
LAUNCHER_NAME = "Launch-SonicHeroes.cmd"
HEROES_TITLE_ID = 0x5345002B
EXE_NAME = "SonicHeroesPC.exe"
# XDK 5849 D3D device layout: the submitted counter and the pointer to the
# GPU-read word (docs/technical/d3d8ltcg-device-context.md). Only the address
# of the device pointer itself is title-specific.
DEFAULT_D3D_PUT_OFF = 0x2C
DEFAULT_D3D_GET_PTR_OFF = 0x30
CHUNK = 1024 * 1024
MAX_DISC_BYTES = 8 * 1024 * 1024 * 1024
MAX_DISC_FILES = 100_000
SOURCE_SUFFIXES = {
    ".c", ".h", ".py", ".md", ".txt", ".cmake", ".sh", ".bat", ".cmd",
    ".in", ".toml", ".yml", ".yaml", ".rc", ".manifest", ".cpp", ".hpp",
}
SOURCE_NAMES = {"LICENSE", "NOTICE", "CMakeLists.txt", ".gitignore", ".gitattributes"}
EXCLUDED_PARTS = {".git", ".work", ".venv", ".cache", "build", "dist", "out",
                  "node_modules", "__pycache__", "game_files", "artifacts"}
GAME_SUFFIXES = {".iso", ".xiso", ".xbe", ".one", ".sfd", ".xmv", ".wav", ".adx"}
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL",
                    *(f"COM{i}" for i in range(1, 10)),
                    *(f"LPT{i}" for i in range(1, 10))}


class PipelineError(Exception):
    pass


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PipelineError(f"Cannot read {path.name}.") from exc


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _run(argv: list[str], log: Path, *, cwd: Path = ROOT) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    print(f"[stage] {log.stem}", flush=True)
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    with log.open("w", encoding="utf-8", errors="replace") as stream:
        result = subprocess.run([str(a) for a in argv], cwd=cwd, env=env,
                                stdout=stream, stderr=subprocess.STDOUT)
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines[-(80 if result.returncode else 25):]:
        print(line, flush=True)
    if result.returncode:
        raise PipelineError(f"{log.stem} failed (exit {result.returncode}); see diagnostics/{log.name}.")


def _module(module: str, args: list, log: Path) -> None:
    _run([sys.executable, "-m", module, *args], log)


def _component(name: str) -> str:
    if (not name or name in {".", ".."} or name.endswith((" ", "."))
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
            or name.split(".")[0].upper() in WINDOWS_RESERVED):
        raise PipelineError("The disc contains a filename unsafe for Windows extraction.")
    return name


def _disc_files(iso) -> list[tuple[str, object]]:
    """Walk XDVDFS without trusting tree links, recursion, or host path semantics."""
    image_size = Path(iso.path).stat().st_size
    queue = deque([("", iso.root_sector, iso.root_size, 0)])
    visited = set()
    files = []
    paths = set()
    total = 0
    while queue:
        directory, sector, size, depth = queue.popleft()
        if depth > 32 or (sector, size) in visited or size <= 0:
            raise PipelineError("The disc directory tree is invalid or too deep.")
        visited.add((sector, size))
        if sector * 2048 + size > image_size - iso.base:
            raise PipelineError("The disc directory extends past the end of the image.")
        entries = iso.listdir(directory)
        names = set()
        for entry in entries:
            name = _component(entry.name)
            if name.casefold() in names:
                raise PipelineError("The disc contains case-colliding filenames.")
            names.add(name.casefold())
            relative = f"{directory}/{name}" if directory else name
            if relative.casefold() in paths:
                raise PipelineError("The disc contains duplicate paths.")
            paths.add(relative.casefold())
            if len(paths) > MAX_DISC_FILES:
                raise PipelineError("The disc contains too many entries.")
            if entry.is_dir:
                queue.append((relative, entry.sector, entry.size, depth + 1))
            else:
                if entry.sector * 2048 + entry.size > image_size - iso.base:
                    raise PipelineError("A disc file extends past the end of the image.")
                total += entry.size
                if total > MAX_DISC_BYTES:
                    raise PipelineError("The extracted game exceeds the size limit.")
                files.append((relative, entry))
    return files


def extract_disc(iso_path: Path, game_dir: Path) -> dict:
    from tools.xiso.xdvdfs import Xiso, XisoError

    if game_dir.exists():
        raise PipelineError("The game extraction directory already exists.")
    game_dir.mkdir(parents=True)
    try:
        with Xiso(str(iso_path)) as iso:
            root = iso.find("default.xbe")
            if root is None or root.is_dir:
                raise PipelineError("The image has no default.xbe at its root.")
            iso.extract(root, str(game_dir / "default.xbe"))
            files = _disc_files(iso)
            count = 0
            total = 0
            for relative, entry in files:
                if relative.casefold() == "default.xbe":
                    continue
                destination = game_dir.joinpath(*PureWindowsPath(relative).parts)
                if not destination.resolve().is_relative_to(game_dir.resolve()):
                    raise PipelineError("A disc path escapes the extraction directory.")
                iso.extract(entry, str(destination))
                count += 1
                total += entry.size
            return {"files": count + 1, "bytes": total + root.size,
                    "partition_base": f"0x{iso.base:08X}"}
    except XisoError as exc:
        raise PipelineError(str(exc)) from exc


KNOWN_TITLE_KEYS = {"sonicheroes"}


def _title_key(text: str) -> str:
    """Case- and punctuation-insensitive title key: 'SONICHEROES' == 'Sonic Heroes'."""
    return "".join(ch for ch in text.casefold() if ch.isalnum())


def inspect_xbe(xbe_path: Path, *, require_heroes: bool = True) -> dict:
    from tools.xbe_parser.xbe_parser import XBEParser, format_region

    try:
        xbe = XBEParser(str(xbe_path)).parse()
    except (OSError, ValueError, struct.error, IndexError, UnicodeError) as exc:
        raise PipelineError("The extracted default.xbe is not a readable retail XBE.") from exc
    header, cert = xbe.header, xbe.certificate
    title = " ".join(cert.title_name.split())
    # The title ID is the identity. The certificate's display name is not
    # standardised: the retail disc says "SONICHEROES" (no space, upper case),
    # and regional releases may spell it differently again. Comparing against
    # one literal rejected a correct disc, so the name is only a sanity note.
    if require_heroes and cert.title_id != HEROES_TITLE_ID:
        raise PipelineError(
            f"This workflow expects Sonic Heroes (title ID 5345002B), got "
            f"{title!r} / {cert.title_id:08X}. Use an original Xbox retail image; "
            "the USA, Europe and Japan releases all carry title ID 5345002B.")
    if header.is_debug:
        raise PipelineError("Debug/devkit XBEs are not supported by this retail workflow.")
    code = [section for section in xbe.sections if section.flags & 0x4]
    if not any(s.virtual_addr <= header.entry_point < s.virtual_addr + s.virtual_size
               for s in code):
        raise PipelineError("The XBE entry point is not inside an executable section.")
    if not xbe.kernel_imports:
        raise PipelineError("The XBE has no kernel imports.")
    builds = sorted({lib.build for lib in xbe.libraries if lib.build})
    warnings = []
    if require_heroes and _title_key(title) not in KNOWN_TITLE_KEYS:
        warnings.append(f"Certificate title {title!r} is not a known Sonic Heroes "
                        "spelling; accepted because the title ID matches.")
    if 5849 not in builds:
        warnings.append("XDK 5849 was not reported; runtime structure offsets are not verified for this build.")
    warnings.append("Retail boot is not verified by CI. OGlue's Heroes fixes live in the "
                    "runtime; the fence ones also need the title's D3D device pointer "
                    "(--d3d-device-ptr), which is not set by default.")
    return {
        "title": title or "Synthetic OGlue test",
        "title_id": f"{cert.title_id:08X}",
        # USA/NTSC, Europe/PAL and Japan share one title ID; the region only
        # changes which certificate bit XAPI checks, and the runtime answers
        # with every region plus NTSC-M 60 Hz (src/kernel/kernel_xbox.c).
        "region_flags": f"0x{cert.game_region & 7:X}",
        "region": format_region(cert.game_region),
        "entry_point": f"0x{header.entry_point:08X}",
        "base_address": f"0x{header.base_address:08X}",
        "xdk_builds": builds,
        "sections": len(xbe.sections),
        "code_sections": [s.name for s in code],
        "kernel_imports": len(xbe.kernel_imports),
        "sha256": hash_file(xbe_path),
        "size": xbe_path.stat().st_size,
        "warnings": warnings,
    }


def _source_file(relative: Path) -> bool:
    return (not any(part in EXCLUDED_PARTS for part in relative.parts)
            and relative.suffix.lower() not in GAME_SUFFIXES
            and (relative.suffix.lower() in SOURCE_SUFFIXES
                 or relative.name in SOURCE_NAMES
                 or relative.name.startswith("Dockerfile")))


def _toolkit_files(toolkit: Path) -> list[Path]:
    try:
        top = subprocess.run(["git", "-C", str(toolkit), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, check=True).stdout.strip()
        if Path(top).resolve() == toolkit.resolve():
            raw = subprocess.run(["git", "-C", str(toolkit), "ls-files", "-z",
                                  "--cached", "--others", "--exclude-standard"],
                                 capture_output=True, check=True).stdout
            candidates = sorted({Path(p.decode("utf-8")) for p in raw.split(b"\0") if p})
        else:
            raise ValueError
    except (OSError, subprocess.CalledProcessError, ValueError, UnicodeError):
        candidates = []
        for current, dirs, names in os.walk(toolkit):
            dirs[:] = [d for d in dirs if d not in EXCLUDED_PARTS]
            candidates.extend(Path(current, name).relative_to(toolkit) for name in names)
        candidates.sort()
    result = []
    for relative in candidates:
        if relative.is_absolute() or ".." in relative.parts or not _source_file(relative):
            continue
        path = toolkit / relative
        if path.is_symlink():
            raise PipelineError("Refusing to package a symlink into the source artifact.")
        if path.is_file():
            result.append(relative)
    return result


def source_provenance(toolkit: Path) -> dict:
    try:
        commit = subprocess.run(["git", "-C", str(toolkit), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "-C", str(toolkit), "status", "--porcelain"],
                                    capture_output=True, text=True, check=True).stdout.strip())
        return {"commit": commit, "dirty": dirty}
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "dirty": True}


def _parse_hex(text: str) -> int:
    value = (text or "").strip()
    if not re.fullmatch(r"(?:0[xX])?[0-9a-fA-F]{1,8}", value):
        raise PipelineError("Expected up to 8 hex digits, e.g. 0x0035FB48 or 2C.")
    return int(value, 16)


def parse_guest_va(text: str) -> int:
    address = _parse_hex(text)
    if not 0x10000 <= address < 0x04000000 or address & 3:
        raise PipelineError("The D3D device pointer must be a 4-byte-aligned address "
                            "inside the XBE image (0x00010000..0x03FFFFFF).")
    return address


def parse_offset(text: str) -> int:
    offset = _parse_hex(text)
    if offset > 0xFFFF or offset & 3:
        raise PipelineError("A device field offset must be 4-byte aligned and below 0xFFFF.")
    return offset


def fence_config(device_ptr: str, put_off: str = "", get_ptr_off: str = "") -> dict | None:
    """The one per-title datum the runtime's Heroes fence fixes need.

    xbox_Nv2aMirrorFence() has no caller in src/: a game project registers the
    address of its D3D device pointer. Without it xbox_Nv2aFenceWordVa() and
    xbox_Nv2aBusyEventVa() return 0, so GENRY_HONEST_FENCE and the busy-block
    wait in kernel_bridge.c stay dormant.
    """
    if not (device_ptr or "").strip():
        if (put_off or "").strip() or (get_ptr_off or "").strip():
            raise PipelineError("Set d3d_device_ptr as well; offsets alone mean nothing.")
        return None
    put = parse_offset(put_off) if (put_off or "").strip() else DEFAULT_D3D_PUT_OFF
    get = parse_offset(get_ptr_off) if (get_ptr_off or "").strip() else DEFAULT_D3D_GET_PTR_OFF
    return {"device_ptr_va": parse_guest_va(device_ptr), "put_off": put, "get_ptr_off": get}


def scaffold_project(project: Path, metadata: dict, *, toolkit: Path = ROOT,
                     template: Path = TEMPLATE, fence: dict | None = None) -> dict:
    if project.exists():
        raise PipelineError("The project directory already exists; use a fresh work directory.")
    if not (template / "CMakeLists.txt").is_file():
        raise PipelineError("The Sonic Heroes project template is missing.")
    project.mkdir(parents=True)
    for source in template.rglob("*"):
        if source.is_file():
            destination = project / source.relative_to(template)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    runtime = project / "oglue"
    for relative in _toolkit_files(toolkit):
        destination = runtime / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(toolkit / relative, destination)
    entry = int(metadata["entry_point"], 16)
    fence = fence or {}
    device = fence.get("device_ptr_va", 0)
    (project / "src").mkdir(parents=True, exist_ok=True)
    (project / "src" / "game_config.h").write_text(
        "/* Generated from the supplied XBE; do not reuse with another dump. */\n"
        "#ifndef HEROES_GAME_CONFIG_H\n#define HEROES_GAME_CONFIG_H\n"
        f"#define HEROES_ENTRY_POINT 0x{entry:08X}u\n"
        f'#define HEROES_XBE_SHA256 "{metadata["sha256"]}"\n'
        "/* 0 = not supplied: the runtime's fence mirror stays unregistered. */\n"
        f"#define HEROES_D3D_DEVICE_PTR 0x{device:08X}u\n"
        f"#define HEROES_D3D_PUT_OFF 0x{fence.get('put_off', DEFAULT_D3D_PUT_OFF):X}u\n"
        f"#define HEROES_D3D_GET_PTR_OFF 0x{fence.get('get_ptr_off', DEFAULT_D3D_GET_PTR_OFF):X}u\n"
        "#endif\n", encoding="utf-8")
    return source_provenance(toolkit)


def _entry_address(entry: dict) -> int:
    value = entry.get("start", entry.get("address"))
    return int(value, 16) if isinstance(value, str) else int(value)


def _entry_in_dispatch(path: Path, entry: int) -> bool:
    if not path.is_file():
        return False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.search(r"\{\s*0x([0-9a-fA-F]+)u?\s*,", line)
        if match and int(match.group(1), 16) == entry:
            return True
    return False


def translation_gate(summary: dict, dispatch: Path, entry: int, *,
                     strict: bool = False) -> dict:
    """Structural problems stop the build; quality problems are reported.

    A retail title routinely has a few functions the lifter rejects and some
    instructions it emits as comments. Refusing to package on the first one
    turned a usable bring-up build into a hard failure, so by default those
    counts are recorded as warnings (visible in BUILD-INFO.json and the run
    summary) and --strict-translation restores the old behaviour.
    """
    total = summary.get("total", 0)
    translated = summary.get("translated", 0)
    failed = summary.get("failed", 0)
    unimplemented = summary.get("unimplemented") or {}
    unimplemented_count = sum(len(v) if isinstance(v, list) else int(v)
                              for v in unimplemented.values())
    unresolved = summary.get("unresolved_stubs", 0)
    present = _entry_in_dispatch(dispatch, entry)
    result = {
        "total": total, "translated": translated, "failed": failed,
        "unimplemented_instructions": unimplemented_count,
        "unresolved_stubs": unresolved, "entry_in_dispatch": present,
    }
    if total <= 0:
        raise PipelineError("The recompiler translated no functions at all.")
    if not present:
        raise PipelineError("The XBE entry point is absent from the generated dispatch table.")
    problems = []
    if failed or translated != total:
        problems.append(f"{translated}/{total} functions translated, {failed} failed.")
    if unimplemented_count:
        problems.append(f"{unimplemented_count} instructions were emitted as no-ops.")
    if unresolved:
        problems.append(f"{unresolved} unresolved call targets have stubs.")
    result["warnings"] = problems
    result["strict"] = strict
    if problems and strict:
        raise PipelineError("Strict translation gate: " + " ".join(problems))
    for problem in problems:
        print(f"[warn] {problem}", flush=True)
    return result


def recompile(work: Path, state: dict, *, strict: bool = False) -> None:
    game = work / "game"
    xbe = game / "default.xbe"
    analysis = work / "analysis"
    project = work / "project"
    diagnostics = work / "diagnostics"
    disasm = analysis / "disasm"
    func_id = analysis / "func_id"
    abi = analysis / "abi"
    gen = project / "src" / "recomp" / "gen"
    parsed = analysis / "default_analysis.json"
    analysis.mkdir(parents=True, exist_ok=True)
    _module("tools.xbe_parser", [xbe, "--json", parsed], diagnostics / "01-parse.log")
    _module("tools.disasm", [xbe, "--analysis-json", parsed, "-o", disasm, "--force"],
            diagnostics / "02-disassemble.log")
    functions = read_json(disasm / "functions.json")
    entry = int(state["xbe"]["entry_point"], 16)
    if not any(_entry_address(f) == entry for f in functions):
        raise PipelineError("Function recovery did not find the XBE entry point.")
    _module("tools.func_id", [xbe, "--functions", disasm / "functions.json",
                              "--strings", disasm / "strings.json",
                              "--xrefs", disasm / "xrefs.json", "-o", func_id],
            diagnostics / "03-identify.log")
    _module("tools.abi_analysis", [xbe, "--disasm-dir", disasm, "--func-id-dir", func_id,
                                   "--output-dir", abi], diagnostics / "04-abi.log")
    from tools.kernel_audit.gen_ordinal_remap import build_remap, emit_header
    remap, identity, unresolved, total = build_remap(str(parsed))
    emit_header(remap, str(project / "src" / "kernel_ordinal_remap.h"),
                state["xbe"]["title"], str(parsed))
    state["kernel_remap"] = {"imports": total, "identity": identity,
                             "remapped": len(remap),
                             "unresolved": [{"ordinal": o, "name": n} for o, n in unresolved]}
    _module("tools.recomp", [xbe, "--all", "--split", "250", "--game-name", "Sonic Heroes",
                             "--gen-dir", gen, "--disasm-dir", disasm,
                             "--func-id-dir", func_id, "--abi-dir", abi,
                             "--exclude-manual", project / "src" / "recomp_manual.c",
                             "-o", analysis / "recomp"], diagnostics / "05-recompile.log")
    summary = read_json(analysis / "recomp" / "summary.json")
    state["translation"] = translation_gate(summary, gen / "recomp_dispatch.c", entry,
                                            strict=strict)
    write_json(diagnostics / "translation-summary.json", summary)
    state["xbe"]["warnings"].extend(state["translation"]["warnings"])
    if not (gen / "recomp_types.h").is_file() or not list(gen.glob("recomp_[0-9][0-9][0-9][0-9].c")):
        raise PipelineError("The recompiler did not emit its C chunks and runtime header.")
    state["status"] = "recompiled"


def pe_summary(exe: Path) -> dict:
    try:
        data = exe.read_bytes()
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\0\0":
            raise ValueError
        machine = struct.unpack_from("<H", data, pe + 4)[0]
        optional = pe + 24
        magic = struct.unpack_from("<H", data, optional)[0]
        subsystem = struct.unpack_from("<H", data, optional + 68)[0]
    except (OSError, ValueError, struct.error) as exc:
        raise PipelineError("The build did not produce a valid PE executable.") from exc
    if machine != 0x8664 or magic != 0x20B:
        raise PipelineError("The executable is not Windows x64 (PE32+).")
    return {"machine": "x64", "format": "PE32+", "subsystem": subsystem,
            "sha256": hash_file(exe), "size": exe.stat().st_size}


def _copy_state(work: Path, state: dict) -> None:
    write_json(work / "state.json", state)
    write_json(work / "diagnostics" / "build-state.json", state)
    write_json(work / "project" / "BUILD-INFO.json", state)


def _hash_tree(directory: Path, exclude: set[str]) -> list[dict]:
    result = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(directory).as_posix()
        if relative in exclude:
            continue
        result.append({"path": relative, "sha256": hash_file(path), "size": path.stat().st_size})
    return result


def write_game_files_readme(package: Path, state: dict) -> None:
    """The game artifact ships no assets: tell the user exactly what to add."""
    xbe = state.get("xbe", {})
    iso = state.get("iso", {})
    fence = state.get("d3d_fence", {})
    fence_line = (f"зеркало fence включено, указатель устройства {fence.get('device_ptr_va')}"
                  if fence.get("registered") else
                  "зеркало fence НЕ включено (адрес D3D-устройства не задавался)")
    text = f"""# Куда положить файлы игры

В этом архиве **только рекомпилированная игра** — `SonicHeroesPC.exe`. Оригинальные
файлы игры (assets) сюда не входят и не распространяются. Добавьте их сами из
своего образа Sonic Heroes для Original Xbox.

## Из какого образа собрана эта версия

| | |
|---|---|
| Название в сертификате | `{xbe.get('title', '?')}` |
| Title ID | `{xbe.get('title_id', '?')}` |
| Регион | {xbe.get('region', '?')} |
| SHA-256 всего ISO | `{iso.get('sha256', '?')}` |
| SHA-256 `default.xbe` | `{xbe.get('sha256', '?')}` |
| Особенности сборки | {fence_line} |

При запуске игра сверяет SHA-256 `game\\default.xbe` с тем, под который она собрана.
Нужен **тот же самый дамп**, не другой регион и не другая версия.

## Какая структура должна получиться

```
SonicHeroesPC.exe
Launch-SonicHeroes.cmd
HOW-TO-ADD-GAME-FILES.md      <- этот файл
game\\
    default.xbe
    ...все остальные папки и файлы из образа, без переименования
```

## Способ 1 — распаковать ISO встроенным распаковщиком

Нужен Python 3.10+. Распаковщик лежит в архиве исходников
(`SonicHeroes-PC-Recompilation-Source`) в папке `oglue`, либо в этом репозитории.

```bat
cd <папка исходников>\\oglue
python -m tools.xiso unpack "C:\\Images\\SonicHeroes.iso" -o "<папка с exe>\\game"
```

Ничего дополнительно устанавливать не нужно: `tools.xiso` читает образ сам.

## Способ 2 — скопировать уже распакованную папку

Если игра уже распакована (например, с консоли), скопируйте **содержимое** диска
в папку `game` так, чтобы получился `game\\default.xbe`, а рядом лежали остальные
папки образа. Имена и вложенность менять нельзя.

## Проверка

PowerShell из папки с exe:

```powershell
Get-FileHash .\\game\\default.xbe -Algorithm SHA256
```

Значение должно совпасть с SHA-256 `default.xbe` из таблицы выше.

## Запуск

Запустите `Launch-SonicHeroes.cmd`. Управление, требования и диагностика —
в `README-RU.md`. Сохранения: `%LOCALAPPDATA%\\xboxrecomp`.

## Если хеш не совпал

Игра откажется стартовать: этот EXE собран под конкретный дамп. Либо возьмите тот
же образ, из которого шла сборка, либо перезапустите workflow со своим образом —
он соберёт EXE под него.
"""
    (package / "HOW-TO-ADD-GAME-FILES.md").write_text(text, encoding="utf-8")
    (package / "game").mkdir(exist_ok=True)
    (package / "game" / "PUT-GAME-FILES-HERE.txt").write_text(
        "Сюда кладутся файлы игры: default.xbe и остальные папки образа.\n"
        "Подробно: ..\\HOW-TO-ADD-GAME-FILES.md\n"
        f"Ожидаемый SHA-256 default.xbe: {xbe.get('sha256', '?')}\n",
        encoding="utf-8")


def package_game(work: Path, state: dict) -> Path:
    package = work / "artifacts" / "game"
    if package.exists():
        raise PipelineError("The game artifact directory already exists.")
    package.mkdir(parents=True)
    exe = work / "build" / "Release" / EXE_NAME
    shutil.copy2(exe, package / EXE_NAME)
    for name in (LAUNCHER_NAME, "README-RU.md"):
        shutil.copy2(PACKAGE_TEMPLATE / name, package / name)
    write_game_files_readme(package, state)
    # No assets: the extracted tree stays on the runner and is never uploaded.
    for path in package.rglob("*"):
        if path.is_file() and path.suffix.lower() in GAME_SUFFIXES:
            raise PipelineError("Refusing to ship original game data in the game artifact.")
    write_json(package / "BUILD-INFO.json", state)
    sums = _hash_tree(package, {"SHA256SUMS.txt"})
    (package / "SHA256SUMS.txt").write_text(
        "".join(f'{item["sha256"]}  {item["path"]}\n' for item in sums), encoding="utf-8")
    return package


def finalize_source(work: Path, state: dict) -> None:
    """The source artifact is a publishable repo: code, CI, docs -- no launcher."""
    project = work / "project"
    for path in project.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() in GAME_SUFFIXES:
            raise PipelineError("Refusing to upload original game data in the source artifact.")
        if path.name == LAUNCHER_NAME:
            raise PipelineError("The player launcher belongs to the game artifact, not the source.")
    write_json(project / "SOURCE-MANIFEST.json", {
        "note": "Generated C is derived from the user's XBE. Original assets are not included.",
        "files": _hash_tree(project, {"SOURCE-MANIFEST.json"}),
    })


def build(work: Path, jobs: int = 2) -> dict:
    state = read_json(work / "state.json")
    if state.get("status") != "recompiled":
        raise PipelineError("Run prepare successfully before build.")
    if jobs < 1 or jobs > 8:
        raise PipelineError("Build jobs must be between 1 and 8.")
    cmake = shutil.which("cmake")
    if not cmake:
        raise PipelineError("CMake is required (Visual Studio 2022 C++ workload).")
    project, build_dir = work / "project", work / "build"
    diagnostics = work / "diagnostics"
    _run([cmake, "-S", project, "-B", build_dir, "-G", "Visual Studio 17 2022", "-A", "x64"],
         diagnostics / "06-configure.log")
    _run([cmake, "--build", build_dir, "--config", "Release", "--target", "SonicHeroesPC",
          "--parallel", jobs], diagnostics / "07-build.log")
    exe = build_dir / "Release" / EXE_NAME
    state["executable"] = pe_summary(exe)
    state["build"] = {"configuration": "Release", "platform": "Windows x64",
                      "generator": "Visual Studio 17 2022", "parallel_jobs": jobs,
                      "cmake_version": subprocess.run([cmake, "--version"], capture_output=True,
                                                      text=True, check=True).stdout.splitlines()[0]}
    state["status"] = "built"
    state["validation"] = {"compiled": True, "pe_checked": True,
                           "runtime_tested": False, "playability_tested": False}
    _copy_state(work, state)
    package_game(work, state)
    finalize_source(work, state)
    print(f"Built {EXE_NAME}; runtime/playability was NOT tested.", flush=True)
    return state


def prepare(iso: Path, work: Path, *, toolkit: Path = ROOT,
            template: Path = TEMPLATE, fence: dict | None = None,
            strict: bool = False) -> dict:
    if (work / "state.json").exists() or (work / "project").exists():
        raise PipelineError("Use a fresh work directory; this command will not overwrite a run.")
    if not iso.is_file():
        raise PipelineError("The ISO file does not exist.")
    work.mkdir(parents=True, exist_ok=True)
    (work / "diagnostics").mkdir(exist_ok=True)
    state = {
        "schema_version": 1, "status": "extracting", "pipeline": "OGlue",
        "source": source_provenance(toolkit),
        "iso": {"sha256": hash_file(iso), "size": iso.stat().st_size},
        "validation": {"compiled": False, "pe_checked": False,
                       "runtime_tested": False, "playability_tested": False},
    }
    write_json(work / "state.json", state)
    state["disc"] = extract_disc(iso, work / "game")
    state["xbe"] = inspect_xbe(work / "game" / "default.xbe")
    state["source"] = scaffold_project(work / "project", state["xbe"],
                                       toolkit=toolkit, template=template, fence=fence)
    state["d3d_fence"] = fence or {"registered": False}
    if fence:
        state["d3d_fence"] = dict(fence, registered=True)
    else:
        state["xbe"]["warnings"].append(
            "No D3D device pointer supplied: the fence mirror is unregistered, so "
            "GENRY_HONEST_FENCE and the D3D busy-block wait stay dormant.")
    state["status"] = "prepared"
    _copy_state(work, state)
    recompile(work, state, strict=strict)
    _copy_state(work, state)
    return state


def _fail(work: Path, message: str) -> None:
    diagnostics = work / "diagnostics"
    diagnostics.mkdir(parents=True, exist_ok=True)
    (diagnostics / "error.txt").write_text(message + "\n", encoding="utf-8")
    state_path = work / "state.json"
    if state_path.is_file():
        try:
            state = read_json(state_path)
            state["status"] = "failed"
            write_json(state_path, state)
            write_json(diagnostics / "build-state.json", state)
        except PipelineError:
            pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "build", "all"):
        p = sub.add_parser(name)
        p.add_argument("--work-dir", type=Path, required=True)
        if name != "build":
            p.add_argument("--iso", type=Path, required=True)
            p.add_argument("--strict-translation", action="store_true",
                           help="refuse to package when any function failed to translate "
                                "or any instruction became a no-op (default: warn)")
            p.add_argument("--d3d-device-ptr", default="",
                           help="guest VA of the title's D3D device pointer, e.g. 0x0035FB48; "
                                "registers the runtime fence mirror (Heroes fence fixes)")
            p.add_argument("--d3d-put-off", default="",
                           help=f"device submitted-counter offset (default 0x{DEFAULT_D3D_PUT_OFF:X})")
            p.add_argument("--d3d-get-ptr-off", default="",
                           help=f"device GPU-read-pointer offset (default 0x{DEFAULT_D3D_GET_PTR_OFF:X})")
        if name != "prepare":
            p.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args(argv)
    work = args.work_dir.resolve()
    try:
        if args.command in ("prepare", "all"):
            prepare(args.iso.resolve(), work,
                    fence=fence_config(args.d3d_device_ptr, args.d3d_put_off,
                                       args.d3d_get_ptr_off),
                    strict=args.strict_translation)
        if args.command in ("build", "all"):
            build(work, args.jobs)
    except PipelineError as exc:
        _fail(work, str(exc))
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        return 1
    except KeyboardInterrupt:
        _fail(work, "Interrupted.")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
