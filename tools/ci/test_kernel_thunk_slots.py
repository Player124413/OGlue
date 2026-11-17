"""Exercise legacy thunk slot selection with a compiled, platform-free harness."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_mapped_and_fallback_slots_are_bounded(tmp_path):
    compiler = shutil.which("cc")
    if not compiler:
        pytest.skip("C compiler not available")
    source = tmp_path / "slots.c"
    source.write_text(r'''
#include <assert.h>
#include "src/kernel/kernel_thunk_slots.h"
int main(void) {
    const uint32_t mapped[] = {0x80000025u, 0x80000090u, 0, 0x80000011u};
    const uint32_t fallback[] = {1, 2, 3};
    uint32_t ordinal = 999;
    assert(xbox_legacy_thunk_ordinal(mapped, 2, fallback, 3, 0, &ordinal) && ordinal == 37);
    assert(xbox_legacy_thunk_ordinal(mapped, 2, fallback, 3, 1, &ordinal) && ordinal == 144);
    /* Never read fallback after a real table, including an empty one. */
    assert(!xbox_legacy_thunk_ordinal(mapped, 2, fallback, 3, 2, &ordinal));
    assert(!xbox_legacy_thunk_ordinal(mapped, 0, fallback, 3, 0, &ordinal));
    assert(!xbox_legacy_thunk_ordinal(mapped, 4, fallback, 3, 2, &ordinal));
    assert(xbox_legacy_thunk_ordinal(0, 0, fallback, 3, 2, &ordinal) && ordinal == 3);
    assert(!xbox_legacy_thunk_ordinal(0, 0, fallback, 3, 3, &ordinal));
    /* Examples of ordinary high-bit data seen immediately before the XBE
     * table, plus legitimate encoded values used by existing titles. */
    assert(!xbox_plausible_extra_thunk(0xc3c95bc3u, 378));
    assert(!xbox_plausible_extra_thunk(0x8b5e5f00u, 378));
    assert(!xbox_plausible_extra_thunk(0x80000000u, 378));
    assert(!xbox_plausible_extra_thunk(0x00000034u, 378));
    assert(xbox_plausible_extra_thunk(0x80000034u, 378));
    assert(xbox_plausible_extra_thunk(0x80000005u, 378));
    return 0;
}
''', encoding="utf-8")
    executable = tmp_path / "slots"
    subprocess.run([compiler, "-std=c99", "-Wall", "-Wextra", "-Werror",
                    "-I", str(ROOT), str(source), "-o", str(executable)], check=True)
    subprocess.run([str(executable)], check=True)
