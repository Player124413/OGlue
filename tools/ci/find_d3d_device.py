#!/usr/bin/env python3
"""Find candidate addresses of the D3D device pointer in a user's own XBE.

The runtime's fence fixes need one per-title number: the guest address of the
global cell that holds the pointer to the D3D device object. It is the address
of the CELL, not of the device: xbox_Nv2aMirrorFence() reads

    dev = *(uint32_t *)device_ptr_va;

and then follows dev+0x30 (a pointer to the GPU-read word) and dev+0x2C (the
submitted counter). See docs/technical/d3d8ltcg-device-context.md.

The stock XDK D3D fence wait reads both fields off one register that was loaded
from a global, so this scans executable sections for that shape:

    mov  eax, [0x0035FB48]   <- the cell we are looking for
    mov  ecx, [eax+0x30]
    mov  edx, [eax+0x2C]
    ...
    lea  ecx, [eax+0x1DCC]   <- D3D's busy KEVENT, a strong extra hint

This is a HEURISTIC. It prints ranked candidates with the code address that
produced each one; it never writes an address into a build. Verify the choice
by running the game: the log line "NV2A fence mirror: device at 0x..." plus a
title that gets past NOW LOADING is the confirmation.

    python -m tools.ci.find_d3d_device game/default.xbe
    python -m tools.ci.find_d3d_device game/default.xbe --json candidates.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))

PUT_OFF = 0x2C
GET_PTR_OFF = 0x30
BUSY_OFF = 0x1DCC
WINDOW = 64          # instructions a register's origin stays interesting
IMAGE_LO = 0x00010000
IMAGE_HI = 0x04000000


def _plausible_cell(va: int) -> bool:
    return IMAGE_LO <= va < IMAGE_HI and not (va & 3)


def scan_code(code: bytes, base_va: int, *, put_off: int = PUT_OFF,
              get_ptr_off: int = GET_PTR_OFF, busy_off: int = BUSY_OFF,
              window: int = WINDOW) -> list[dict]:
    """Return candidates found in one block of x86 code.

    Each candidate: {"kind": "global"|"immediate", "value": int,
                     "evidence_va": int, "busy_event": bool}.
    "global" means value is already the cell address; "immediate" means value
    is the device itself and the caller must look for a cell pointing at it.
    """
    from capstone import Cs, CS_ARCH_X86, CS_MODE_32
    from capstone.x86 import X86_OP_MEM, X86_OP_REG, X86_OP_IMM

    md = Cs(CS_ARCH_X86, CS_MODE_32)
    md.detail = True
    md.skipdata = True          # keep walking past data pools inside code

    origin: dict[int, tuple[str, int, int]] = {}   # reg -> (kind, value, index)
    disps: dict[int, set[int]] = {}
    found: dict[tuple[str, int], dict] = {}

    for index, ins in enumerate(md.disasm(code, base_va)):
        ops = ins.operands if ins.mnemonic != ".byte" else []

        # 1. Field reads off a register whose origin we know.
        for op in ops:
            if op.type != X86_OP_MEM:
                continue
            base = op.mem.base
            if base and base in origin and op.mem.index == 0 and op.mem.disp > 0:
                disps.setdefault(base, set()).add(op.mem.disp)

        # 2. A candidate is a register that touched both fence fields.
        for reg, seen in list(disps.items()):
            if not ({put_off, get_ptr_off} <= seen):
                continue
            kind, value, born = origin[reg]
            if index - born > window or not _plausible_cell(value):
                continue
            key = (kind, value)
            busy = busy_off in seen
            item = found.get(key)
            if item is None or (busy and not item["busy_event"]):
                found[key] = {"kind": kind, "value": value,
                              "evidence_va": ins.address, "busy_event": busy}

        # 3. Anything written here loses its old origin.
        try:
            _, written = ins.regs_access()
        except Exception:
            written = ()
        for reg in written:
            origin.pop(reg, None)
            disps.pop(reg, None)

        # 4. Track the two ways a device reaches a register.
        if ins.mnemonic == "mov" and len(ops) == 2 and ops[0].type == X86_OP_REG:
            dst, src = ops[0].reg, ops[1]
            if (src.type == X86_OP_MEM and src.mem.base == 0 and src.mem.index == 0
                    and _plausible_cell(src.mem.disp)):
                origin[dst] = ("global", src.mem.disp, index)
                disps[dst] = set()
            elif src.type == X86_OP_IMM and _plausible_cell(src.imm):
                origin[dst] = ("immediate", src.imm, index)
                disps[dst] = set()

    return sorted(found.values(), key=lambda c: (not c["busy_event"], c["value"]))


def _cells_pointing_at(data: bytes, sections, device_va: int) -> list[int]:
    """Data words equal to a statically addressed device (Burnout 3's shape)."""
    import struct

    cells = []
    for sec in sections:
        if sec.flags & 0x4:
            continue
        blob = data[sec.raw_addr:sec.raw_addr + sec.raw_size]
        for offset in range(0, max(0, len(blob) - 3), 4):
            if struct.unpack_from("<I", blob, offset)[0] == device_va:
                va = sec.virtual_addr + offset
                if _plausible_cell(va):
                    cells.append(va)
    return cells


def find_candidates(xbe_path: Path, *, put_off: int = PUT_OFF,
                    get_ptr_off: int = GET_PTR_OFF) -> list[dict]:
    """Scan a user's own XBE. Needs no analysis JSON and writes nothing."""
    import struct

    from tools.xbe_parser.xbe_parser import XBEParser

    xbe = XBEParser(str(xbe_path)).parse()
    data, sections = xbe.raw_data, xbe.sections
    results: dict[int, dict] = {}
    for sec in sections:
        if not (sec.flags & 0x4) or not sec.raw_size:
            continue
        blob = data[sec.raw_addr:sec.raw_addr + sec.raw_size]
        for hit in scan_code(blob, sec.virtual_addr, put_off=put_off,
                             get_ptr_off=get_ptr_off):
            cells = ([hit["value"]] if hit["kind"] == "global"
                     else _cells_pointing_at(data, sections, hit["value"]))
            for cell in cells:
                section = next((s.name for s in sections
                                if s.virtual_addr <= cell
                                < s.virtual_addr + s.virtual_size), "?")
                score = 2 if hit["busy_event"] else 1
                score += 1 if hit["kind"] == "global" else 0
                # D3D's own device pointer lives in the D3D library's data and is
                # read by that library's code. A game object that merely happens
                # to have fields at +0x2C/+0x30 sits in .data and is read from
                # .text -- the common false positive.
                same_library = section == sec.name and section.upper().startswith("D3D")
                if same_library:
                    score += 2
                previous = results.get(cell)
                if previous is None or score > previous["score"]:
                    results[cell] = {
                        "device_ptr_va": f"0x{cell:08X}",
                        "section": section,
                        "code_section": sec.name,
                        "evidence_va": f"0x{hit['evidence_va']:08X}",
                        "busy_event_hint": hit["busy_event"],
                        "origin": hit["kind"],
                        "d3d_library_cell": same_library,
                        "score": score,
                    }
    # The real device pointer is referenced all over the library; a coincidental
    # game object is not. Count absolute-address occurrences in code as a
    # tie-breaker, and report it so the choice is visible rather than implied.
    for item in results.values():
        needle = struct.pack("<I", int(item["device_ptr_va"], 16))
        item["code_references"] = _count_references(data, sections, needle)
    return sorted(results.values(),
                  key=lambda c: (-c["score"], -c["code_references"], c["device_ptr_va"]))


def _count_references(data: bytes, sections, needle: bytes) -> int:
    total = 0
    for sec in sections:
        if not (sec.flags & 0x4):
            continue
        blob = data[sec.raw_addr:sec.raw_addr + sec.raw_size]
        start = blob.find(needle)
        while start != -1:
            total += 1
            start = blob.find(needle, start + 1)
    return total


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("xbe", type=Path, help="your extracted default.xbe")
    parser.add_argument("--json", type=Path, help="also write the candidates here")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args(argv)
    try:
        candidates = find_candidates(args.xbe)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(candidates, indent=2) + "\n", encoding="utf-8")
    if not candidates:
        print("No candidates. The fence wait in this XBE does not match the stock "
              "XDK shape; find the device pointer by hand (see "
              "docs/SONIC_HEROES_PC_RU.md).")
        return 0
    print("Candidates for --d3d-device-ptr (heuristic, verify by running):")
    for item in candidates[:max(1, args.limit)]:
        hint = " +busy KEVENT" if item["busy_event_hint"] else ""
        hint += " +D3D-library cell" if item.get("d3d_library_cell") else ""
        print(f"  {item['device_ptr_va']}  score {item['score']}  "
              f"refs {item.get('code_references', 0)}  "
              f"cell in {item['section']}, from {item['code_section']}:"
              f"{item['evidence_va']}{hint}")
    best = candidates[0]["device_ptr_va"]
    print(f"\nStart with:  --d3d-device-ptr {best}")
    print("The build log must then show: NV2A fence mirror: device at "
          f"{best}, PUT +0x2C -> *(GET +0x30)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
