import json
import struct
from pathlib import Path

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


def _heroes_xbe(tmp_path, title, title_id=H.HEROES_TITLE_ID):
    """A synthetic XBE carrying a real-looking certificate title/ID."""
    data = bytearray(synthetic_xbe())
    struct.pack_into("<I", data, 0x208, title_id)
    data[0x20C:0x25C] = b"\0" * 80
    encoded = title.encode("utf-16-le")
    data[0x20C:0x20C + len(encoded)] = encoded
    path = tmp_path / "default.xbe"
    path.write_bytes(data)
    return path


@pytest.mark.parametrize("title", ["SONICHEROES", "Sonic Heroes", "sonic heroes",
                                   "  SONIC  HEROES "])
def test_accepts_the_retail_certificate_spelling(tmp_path, title):
    # Regression: the retail disc says "SONICHEROES" with no space, and the
    # old exact-name check rejected a correct USA image.
    metadata = H.inspect_xbe(_heroes_xbe(tmp_path, title))
    assert metadata["title_id"] == "5345002B"
    assert not any("spelling" in w for w in metadata["warnings"])


def test_unknown_spelling_with_matching_id_is_a_warning_not_a_failure(tmp_path):
    metadata = H.inspect_xbe(_heroes_xbe(tmp_path, "ソニックヒーローズ"))
    assert any("spelling" in w for w in metadata["warnings"])


def test_wrong_title_id_is_still_rejected(tmp_path):
    with pytest.raises(H.PipelineError, match="5345002B"):
        H.inspect_xbe(_heroes_xbe(tmp_path, "SONICHEROES", title_id=0x53450086))


@pytest.mark.parametrize("flag,name", [(1, "North America"), (2, "Japan"),
                                       (4, "Rest of World")])
def test_records_disc_region(tmp_path, flag, name):
    # USA / Japan / Europe releases share title ID 5345002B and differ only in
    # this certificate bit, so all three must reach the pipeline.
    data = bytearray(synthetic_xbe())
    struct.pack_into("<I", data, 0x200 + 160, flag)
    xbe = tmp_path / "default.xbe"
    xbe.write_bytes(data)
    metadata = H.inspect_xbe(xbe, require_heroes=False)
    assert metadata["region"] == name
    assert metadata["region_flags"] == f"0x{flag:X}"


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


def test_quality_problems_warn_by_default_and_fail_when_strict(tmp_path):
    dispatch = _dispatch(tmp_path)
    summary = {"total": 2, "translated": 1, "failed": 1,
               "unimplemented": {"bsf": ["0x11000"]}, "unresolved_stubs": 3}
    result = H.translation_gate(summary, dispatch, TEXT_VA)
    assert result["strict"] is False
    assert len(result["warnings"]) == 3
    with pytest.raises(H.PipelineError, match="Strict translation gate"):
        H.translation_gate(summary, dispatch, TEXT_VA, strict=True)


def test_structural_problems_always_fail(tmp_path):
    with pytest.raises(H.PipelineError, match="entry point"):
        H.translation_gate({"total": 1, "translated": 1, "failed": 0},
                           _dispatch(tmp_path, TEXT_VA + 16), TEXT_VA)
    with pytest.raises(H.PipelineError, match="no functions"):
        H.translation_gate({"total": 0, "translated": 0, "failed": 0},
                           _dispatch(tmp_path), TEXT_VA)


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


def test_fence_config_defaults_and_validation():
    assert H.fence_config("") is None
    assert H.fence_config("  ") is None
    fence = H.fence_config("0x0035FB48")
    assert fence == {"device_ptr_va": 0x0035FB48, "put_off": 0x2C, "get_ptr_off": 0x30}
    assert H.fence_config("35FB48", "2C", "30") == fence
    with pytest.raises(H.PipelineError, match="offsets alone"):
        H.fence_config("", "2C")
    for bad in ("0x10", "0x04000000", "0x0035FB49", "nonsense", "0x123456789"):
        with pytest.raises(H.PipelineError):
            H.fence_config(bad)
    with pytest.raises(H.PipelineError, match="offset"):
        H.fence_config("0x0035FB48", "0x2E")


def test_scaffold_records_fence_pointer(tmp_path):
    toolkit, template = tmp_path / "toolkit", tmp_path / "template"
    (toolkit / "src").mkdir(parents=True)
    (template / "src").mkdir(parents=True)
    (toolkit / "CMakeLists.txt").write_text("project(test C)\n")
    (template / "CMakeLists.txt").write_text("project(SonicHeroesPC C)\n")
    metadata = {"entry_point": f"0x{TEXT_VA:08X}", "sha256": "b" * 64}
    H.scaffold_project(tmp_path / "off", metadata, toolkit=toolkit, template=template)
    assert "#define HEROES_D3D_DEVICE_PTR 0x00000000u" in (
        tmp_path / "off" / "src" / "game_config.h").read_text()
    H.scaffold_project(tmp_path / "on", metadata, toolkit=toolkit, template=template,
                       fence=H.fence_config("0x0035FB48"))
    config = (tmp_path / "on" / "src" / "game_config.h").read_text()
    assert "#define HEROES_D3D_DEVICE_PTR 0x0035FB48u" in config
    assert "#define HEROES_D3D_PUT_OFF 0x2Cu" in config
    assert "#define HEROES_D3D_GET_PTR_OFF 0x30u" in config


def _fake_built_work(tmp_path):
    work = tmp_path / "work"
    (work / "build" / "Release").mkdir(parents=True)
    (work / "build" / "Release" / H.EXE_NAME).write_bytes(b"MZ-fake-exe")
    (work / "game").mkdir()
    (work / "game" / "default.xbe").write_bytes(b"original game code")
    (work / "game" / "media.xmv").write_bytes(b"original video")
    state = {"xbe": {"title": "SONICHEROES", "title_id": "5345002B",
                     "region": "North America", "sha256": "c" * 64},
             "iso": {"sha256": "d" * 64},
             "d3d_fence": {"registered": False}}
    return work, state


def test_game_artifact_ships_no_assets_and_explains_where_to_put_them(tmp_path):
    work, state = _fake_built_work(tmp_path)
    package = H.package_game(work, state)
    names = {p.relative_to(package).as_posix() for p in package.rglob("*") if p.is_file()}
    assert H.EXE_NAME in names
    assert "HOW-TO-ADD-GAME-FILES.md" in names
    assert "game/PUT-GAME-FILES-HERE.txt" in names
    # Nothing from the extracted disc: no XBE, no video, no data tree.
    assert not any(Path(n).suffix.lower() in H.GAME_SUFFIXES for n in names)
    assert "game/default.xbe" not in names
    readme = (package / "HOW-TO-ADD-GAME-FILES.md").read_text(encoding="utf-8")
    assert "c" * 64 in readme                     # expected XBE hash
    assert "tools.xiso unpack" in readme          # how to extract
    assert "SONICHEROES" in readme


def test_game_artifact_readme_records_the_fence_state(tmp_path):
    work, state = _fake_built_work(tmp_path)
    state["d3d_fence"] = {"registered": True, "device_ptr_va": 0x0033BC38}
    package = H.package_game(work, state)
    readme = (package / "HOW-TO-ADD-GAME-FILES.md").read_text(encoding="utf-8")
    assert "зеркало fence включено" in readme


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
