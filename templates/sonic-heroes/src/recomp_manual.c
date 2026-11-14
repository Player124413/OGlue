/* Required game-side callbacks. No Heroes-specific overrides are invented. */
#include <stdio.h>
#include <stdlib.h>
#include "recomp_types.h"

extern volatile uint32_t g_icall_trace[16];
extern volatile uint32_t g_icall_trace_idx;
extern volatile uint64_t g_icall_count;

recomp_func_t recomp_lookup_manual(uint32_t xbox_va)
{
    (void)xbox_va;
    return NULL;
}

void recomp_icall_fail_log(uint32_t va)
{
    fprintf(stderr, "[ICALL] Failed to resolve VA 0x%08X (call #%llu)\n",
            va, (unsigned long long)g_icall_count);
    for (int i = 0; i < 16; ++i) {
        uint32_t target = g_icall_trace[(g_icall_trace_idx - 16 + i) & 15];
        if (target)
            fprintf(stderr, "  recent[%d] 0x%08X\n", i, target);
    }
    fflush(stderr);
}

void recomp_icall_not_code_log(uint32_t va)
{
    enum { SLOTS = 16 };
    static uint32_t seen[SLOTS];
    static uint64_t hits[SLOTS];
    static int count;
    int i;
    for (i = 0; i < count; ++i)
        if (seen[i] == va) break;
    if (i == count) {
        if (count == SLOTS) return;
        seen[count] = va;
        hits[count] = 0;
        ++count;
    }
    uint64_t n = ++hits[i];
    while (n >= 10 && n % 10 == 0) n /= 10;
    if (n != 1) return;
    fprintf(stderr, "[ICALL] target 0x%08X is not code; skipped %llu time(s)\n",
            va, (unsigned long long)hits[i]);
    fflush(stderr);
}

void recomp_unimpl(const char* text, uint32_t va)
{
    static int printed;
    const char* trap = getenv("RECOMP_UNIMPL_TRAP");
    int stop = trap && *trap && *trap != '0';
    if (printed < 50 || stop) {
        ++printed;
        fprintf(stderr, "[UNIMPL] `%s` reached at 0x%08X\n", text, va);
        fflush(stderr);
    }
    if (stop) abort();
}
