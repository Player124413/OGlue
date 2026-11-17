/* Selection of legacy host thunk slots. Kept independent of the platform so
 * bounds and terminators can be tested without a Windows host or an XBE. */
#ifndef OGLUE_KERNEL_THUNK_SLOTS_H
#define OGLUE_KERNEL_THUNK_SLOTS_H

#include <stddef.h>
#include <stdint.h>

/* If a mapped table is supplied, it is the only source, even when its count
 * is zero or a terminator is encountered. Never fall through to the unrelated
 * reference list after the end of a real game's imports. */
static inline int xbox_legacy_thunk_ordinal(const volatile uint32_t *mapped,
                                     size_t mapped_count,
                                     const uint32_t *fallback,
                                     size_t fallback_count,
                                     size_t index, uint32_t *ordinal)
{
    uint32_t value;
    if (mapped) {
        if (index >= mapped_count) return 0;
        value = mapped[index];
        if (!(value & 0x80000000u)) return 0;
        value &= 0x7fffffffu;
    } else {
        if (index >= fallback_count) return 0;
        value = fallback[index];
    }
    if (!value) return 0;
    *ordinal = value;
    return 1;
}

/* A high bit alone is not enough: arbitrary preceding bytes may be code or
 * data. Only the encoded ordinal range is plausible, though a matching value
 * is still not proof it is an import. */
static inline int xbox_plausible_extra_thunk(uint32_t value, uint32_t max_ordinal)
{
    uint32_t ordinal = value & 0x7fffffffu;
    return (value & 0x80000000u) && ordinal != 0 && ordinal <= max_ordinal;
}

#endif
