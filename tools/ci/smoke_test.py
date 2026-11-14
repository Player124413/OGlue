#!/usr/bin/env python3
"""Exercise the real CI pipeline with a tiny synthetic XBE, never retail data.

Windows CI runs this through MSVC/link/package. --generate-only lets a POSIX
host check the Python half without pretending the Windows runtime was built.
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))

from tools.ci import heroes_build as H
from tools.conformance.mkxbe import BASE, TEXT_VA, build
from tools.xbe_parser.xbe_parser import ENTRY_RETAIL_XOR, THUNK_RETAIL_XOR


def synthetic_xbe() -> bytes:
    data = bytearray(build())
    struct.pack_into("<I", data, 0x128, TEXT_VA ^ ENTRY_RETAIL_XOR)
    thunk_va = BASE + 0x800
    struct.pack_into("<I", data, 0x158, thunk_va ^ THUNK_RETAIL_XOR)
    struct.pack_into("<I", data, 0x800, 0x80000000 | 17)  # ExFreePool
    struct.pack_into("<I", data, 0x804, 0)
    title = "OGLUE-SMOKE".encode("utf-16-le")
    struct.pack_into("<I", data, 0x208, 0x12345678)
    data[0x20C:0x20C + len(title)] = title
    return bytes(data)


def run(work: Path, jobs: int, generate_only: bool) -> None:
    if work.exists():
        raise H.PipelineError("Smoke test needs a fresh work directory.")
    game = work / "game"
    game.mkdir(parents=True)
    (work / "diagnostics").mkdir()
    xbe = game / "default.xbe"
    xbe.write_bytes(synthetic_xbe())
    metadata = H.inspect_xbe(xbe, require_heroes=False)
    state = {
        "schema_version": 1, "status": "prepared", "pipeline": "OGlue synthetic smoke",
        "source": H.source_provenance(H.ROOT),
        "iso": {"sha256": H.hash_file(xbe), "size": xbe.stat().st_size},
        "disc": {"files": 1, "bytes": xbe.stat().st_size},
        "xbe": metadata,
        "validation": {"compiled": False, "pe_checked": False,
                       "runtime_tested": False, "playability_tested": False},
    }
    state["source"] = H.scaffold_project(work / "project", metadata)
    H._copy_state(work, state)
    H.recompile(work, state)
    H._copy_state(work, state)
    if generate_only:
        print("Synthetic generation OK; Windows compile/link was NOT run.")
        return
    state = H.build(work, jobs)
    package = work / "artifacts" / "game"
    assert (package / H.EXE_NAME).is_file()
    assert (package / "game" / "default.xbe").read_bytes() == xbe.read_bytes()
    assert state["validation"]["compiled"] and state["validation"]["pe_checked"]
    assert not state["validation"]["runtime_tested"]
    assert not list((work / "project").rglob("*.xbe"))
    print("Synthetic Windows compile/link/package OK. No retail game was used or executed.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--generate-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        run(args.work_dir.resolve(), args.jobs, args.generate_only)
    except (H.PipelineError, AssertionError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
