import json
import struct

import pytest

from tools.conformance.mkxbe import TEXT_VA
from tools.xiso.test_xdvdfs import _build_image
from . import heroes_build as H
from .smoke_test import synthetic_xbe


def test_extracts_complete_tree_and_counts_bytes(tmp_path):
    xbe = synthetic_xbe()
    image = _build_image(files=(("default.xbe", xbe),),
                         subdir=("media", (("intro.bik", b"BIK1"),)))
    iso = tmp_path / "game.iso"
    iso.write_bytes(image)
    result = H.extract_disc(iso, tmp_path / "game")
    assert (tmp_path / "game" / "default.xbe").read_bytes() == xbe
    assert (tmp_path / "game" / "media" / "intro.bik").read_bytes() == b"BIK1"
    assert result["files"] == 2
    assert result["bytes"] == len(xbe) + 4


def test_rejects_path_traversal(tmp_path):
    image = _build_image(files=(("default.xbe", synthetic_xbe()), ("../evil.txt", b"no")))
    iso = tmp_path / "game.iso"
    iso.write_bytes(image)
    with pytest.raises(H.PipelineError, match="unsafe"):
        H.extract_disc(iso, tmp_path / "game")
    assert not (tmp_path / "evil.txt").exists()


def test_rejects_non_xiso(tmp_path):
    iso = tmp_path / "game.iso"
    iso.write_bytes(b"not an xbox disc" * 4096)
    with pytest.raises(H.PipelineError, match="XDVDFS"):
        H.extract_disc(iso, tmp_path / "game")


def test_inspects_synthetic_but_rejects_wrong_title(tmp_path):
    xbe = tmp_path / "default.xbe"
    xbe.write_bytes(synthetic_xbe())
    with pytest.raises(H.PipelineError, match="Sonic Heroes"):
        H.inspect_xbe(xbe)
    metadata = H.inspect_xbe(xbe, require_heroes=False)
    assert metadata["entry_point"] == f"0x{TEXT_VA:08X}"
    assert metadata["sha256"] == H.hash_file(xbe)
    assert metadata["kernel_imports"] == 1


def _dispatch(tmp_path, entry=TEXT_VA):
    path = tmp_path / "recomp_dispatch.c"
    path.write_text("static const table[] = {\n"
                    f"    {{ 0x{entry:08X}u, (fn)sub_00011000 }},\n}};\n")
    return path


def test_translation_gate_accepts_complete_output(tmp_path):
    result = H.translation_gate({"total": 2, "translated": 2, "failed": 0},
                                _dispatch(tmp_path), TEXT_VA)
    assert result["entry_in_dispatch"] is True
    assert result["unimplemented_instructions"] == 0


def test_translation_gate_rejects_silent_noops_and_missing_entry(tmp_path):
    dispatch = _dispatch(tmp_path)
    with pytest.raises(H.PipelineError, match="no-ops"):
        H.translation_gate({"total": 1, "translated": 1, "failed": 0,
                            "unimplemented": {"bsf": ["0x11000"]}}, dispatch, TEXT_VA)
    with pytest.raises(H.PipelineError, match="entry point"):
        H.translation_gate({"total": 1, "translated": 1, "failed": 0},
                           _dispatch(tmp_path, TEXT_VA + 16), TEXT_VA)
    with pytest.raises(H.PipelineError, match="incomplete"):
        H.translation_gate({"total": 2, "translated": 1, "failed": 1}, dispatch, TEXT_VA)


def test_source_allowlist_and_symlink_refusal(tmp_path):
    toolkit = tmp_path / "toolkit"
    (toolkit / "src").mkdir(parents=True)
    (toolkit / "src" / "runtime.c").write_text("int x;\n")
    (toolkit / "README.md").write_text("source\n")
    (toolkit / "game.iso").write_bytes(b"asset")
    (toolkit / "default.xbe").write_bytes(b"asset")
    (toolkit / ".env").write_text("secret=no")
    files = H._toolkit_files(toolkit)
    assert {p.as_posix() for p in files} == {"README.md", "src/runtime.c"}
    (toolkit / "src" / "link.c").symlink_to(toolkit / "src" / "runtime.c")
    with pytest.raises(H.PipelineError, match="symlink"):
        H._toolkit_files(toolkit)


def test_scaffold_writes_exact_entry_and_source_manifest(tmp_path):
    toolkit = tmp_path / "toolkit"
    template = tmp_path / "template"
    (toolkit / "src").mkdir(parents=True)
    (template / "src").mkdir(parents=True)
    (toolkit / "CMakeLists.txt").write_text("project(test C)\n")
    (toolkit / "src" / "runtime.c").write_text("int runtime;\n")
    (template / "CMakeLists.txt").write_text("project(SonicHeroesPC C)\n")
    (template / "src" / "main.c").write_text("int main(void){return 0;}\n")
    metadata = {"entry_point": f"0x{TEXT_VA:08X}", "sha256": "a" * 64}
    project = tmp_path / "work" / "project"
    H.scaffold_project(project, metadata, toolkit=toolkit, template=template)
    config = (project / "src" / "game_config.h").read_text()
    assert f"0x{TEXT_VA:08X}u" in config and "a" * 64 in config
    assert (project / "oglue" / "src" / "runtime.c").is_file()
    H.finalize_source(tmp_path / "work", {})
    manifest = json.loads((project / "SOURCE-MANIFEST.json").read_text())
    assert any(item["path"] == "src/game_config.h" for item in manifest["files"])
    (project / "default.xbe").write_bytes(b"asset")
    with pytest.raises(H.PipelineError, match="original game data"):
        H.finalize_source(tmp_path / "work", {})


def test_pe_summary_requires_x64_pe32plus(tmp_path):
    exe = tmp_path / "bad.exe"
    exe.write_bytes(b"MZ")
    with pytest.raises(H.PipelineError, match="PE"):
        H.pe_summary(exe)
    data = bytearray(512)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", data, 0x84, 0x8664)
    struct.pack_into("<H", data, 0x98, 0x20B)
    struct.pack_into("<H", data, 0xDC, 2)
    exe.write_bytes(data)
    assert H.pe_summary(exe)["machine"] == "x64"
