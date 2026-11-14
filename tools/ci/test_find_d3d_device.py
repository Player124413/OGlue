import struct

import pytest

from .find_d3d_device import find_candidates, scan_code
from .smoke_test import synthetic_xbe

CELL = 0x0035FB48      # Burnout 3's global pointer cell, used as a fixture value
DEVICE = 0x0035D6A0    # Burnout 3's statically addressed device


def _wait_via_global(cell=CELL):
    return bytes.fromhex(
        "A1" + struct.pack("<I", cell).hex()   # mov eax, [cell]
        + "8B4830"                             # mov ecx, [eax+0x30]
        + "8B502C"                             # mov edx, [eax+0x2C]
        + "3B11"                               # cmp edx, [ecx]
    )


def test_finds_the_global_cell_not_the_device():
    hits = scan_code(_wait_via_global(), 0x00110000)
    assert [h["value"] for h in hits] == [CELL]
    assert hits[0]["kind"] == "global"
    assert hits[0]["busy_event"] is False


def test_busy_event_hint_ranks_first():
    code = (_wait_via_global(0x00200000)
            + bytes.fromhex("8DB8CC1D0000")     # lea edi, [eax+0x1DCC]
            + _wait_via_global(0x00300000))
    # The lea belongs to the second load's register lifetime only if the first
    # origin is still live; either way a busy hint must be reported when present.
    hits = scan_code(code, 0x00110000)
    assert hits, hits
    assert any(h["busy_event"] for h in hits)
    assert hits[0]["busy_event"] is True


def test_immediate_device_is_reported_as_such():
    code = bytes.fromhex(
        "B8" + struct.pack("<I", DEVICE).hex()  # mov eax, DEVICE
        + "8B4830" + "8B502C"
    )
    hits = scan_code(code, 0x00110000)
    assert hits and hits[0]["kind"] == "immediate"
    assert hits[0]["value"] == DEVICE


def test_ignores_unrelated_displacements_and_bad_addresses():
    code = bytes.fromhex("A1" + struct.pack("<I", CELL).hex() + "8B4834" + "8B5028")
    assert scan_code(code, 0x00110000) == []
    # A "global" below the image base is not a cell we may hand the runtime.
    assert scan_code(_wait_via_global(0x00000040), 0x00110000) == []
    # Misaligned cell.
    assert scan_code(_wait_via_global(CELL + 1), 0x00110000) == []


def test_register_reassignment_drops_the_old_origin():
    code = (_wait_via_global()
            + bytes.fromhex("B8" + struct.pack("<I", 0x00220000).hex())  # mov eax, imm
            + bytes.fromhex("8B4830" + "8B502C"))
    hits = scan_code(code, 0x00110000)
    assert {h["value"] for h in hits} == {CELL, 0x00220000}


def test_synthetic_xbe_has_no_candidates(tmp_path):
    xbe = tmp_path / "default.xbe"
    xbe.write_bytes(synthetic_xbe())
    assert find_candidates(xbe) == []


def test_cli_reports_no_candidates_without_failing(tmp_path, capsys):
    from .find_d3d_device import main

    xbe = tmp_path / "default.xbe"
    xbe.write_bytes(synthetic_xbe())
    out = tmp_path / "candidates.json"
    assert main([str(xbe), "--json", str(out)]) == 0
    assert out.is_file()
    assert "No candidates" in capsys.readouterr().out
