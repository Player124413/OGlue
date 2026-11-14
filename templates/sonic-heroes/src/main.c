/*
 * SonicHeroesPC - generic OGlue host generated from the user's XBE.
 *
 * This is a buildable bring-up host, not a claim of a finished Heroes port.
 * OGlue's Heroes fixes are in the shared runtime (DISPATCH gate, DPC timing,
 * pushbuffer ring, honest fence, busy-block wait, threaded vertices). Two of
 * them need one per-title number: the guest address of the D3D device pointer,
 * which a game project hands to xbox_Nv2aMirrorFence(). Nothing in src/ calls
 * it, so this host does -- when HEROES_D3D_DEVICE_PTR was supplied. It is never
 * guessed: a wrong address would name the wrong guest word.
 */
#include <windows.h>
#include <bcrypt.h>
#include <dbghelp.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "kernel/kernel.h"
#include "kernel/xbox_memory_layout.h"
#include "apu/apu.h"
#include "game_config.h"
#include "kernel_ordinal_remap.h"

typedef void (*heroes_entry_fn)(void);
extern heroes_entry_fn recomp_lookup(uint32_t xbox_va);
extern int recomp_dispatch_init(void);
extern RECOMP_TLS uint32_t g_eax, g_ecx, g_edx, g_esp;
extern RECOMP_TLS uint32_t g_ebx, g_esi, g_edi;
extern ptrdiff_t g_xbox_mem_offset;
extern MCPXAPUState *g_apu_state;

static void* load_xbe(const wchar_t* path, size_t* out_size)
{
    FILE* f = NULL;
    if (_wfopen_s(&f, path, L"rb") != 0 || !f)
        return NULL;
    if (fseek(f, 0, SEEK_END) != 0) { fclose(f); return NULL; }
    long size = ftell(f);
    if (size <= 0 || size > 64 * 1024 * 1024 || fseek(f, 0, SEEK_SET) != 0) {
        fclose(f);
        return NULL;
    }
    void* data = malloc((size_t)size);
    if (!data) { fclose(f); return NULL; }
    if (fread(data, 1, (size_t)size, f) != (size_t)size) {
        free(data);
        fclose(f);
        return NULL;
    }
    fclose(f);
    *out_size = (size_t)size;
    return data;
}

/* The generated code and the data sections belong to one exact executable.
 * Refuse a mismatched game folder instead of crashing somewhere in lifted code. */
static int xbe_matches(const void* data, size_t size)
{
    BCRYPT_ALG_HANDLE alg = NULL;
    BCRYPT_HASH_HANDLE hash = NULL;
    unsigned char digest[32];
    char hex[65];
    int ok = 0;
    if (!BCRYPT_SUCCESS(BCryptOpenAlgorithmProvider(&alg, BCRYPT_SHA256_ALGORITHM, NULL, 0)))
        goto done;
    if (!BCRYPT_SUCCESS(BCryptCreateHash(alg, &hash, NULL, 0, NULL, 0, 0)))
        goto done;
    if (!BCRYPT_SUCCESS(BCryptHashData(hash, (PUCHAR)data, (ULONG)size, 0)))
        goto done;
    if (!BCRYPT_SUCCESS(BCryptFinishHash(hash, digest, sizeof(digest), 0)))
        goto done;
    for (int i = 0; i < 32; ++i)
        sprintf_s(hex + i * 2, 3, "%02x", digest[i]);
    hex[64] = '\0';
    ok = strcmp(hex, HEROES_XBE_SHA256) == 0;
done:
    if (hash) BCryptDestroyHash(hash);
    if (alg) BCryptCloseAlgorithmProvider(alg, 0);
    return ok;
}

/* Optional APU voice trace hook (GENRY_FE_STACK). The APU references it even
 * when the environment switch is off, so the host must provide a definition. */
void genry_print_stack(const char* tag)
{
    void* frames[32];
    USHORT count = CaptureStackBackTrace(1, 32, frames, NULL);
    fprintf(stderr, "[FE-STACK] %s\n", tag ? tag : "");
    SYMBOL_INFO* sym = (SYMBOL_INFO*)calloc(1, sizeof(SYMBOL_INFO) + 256);
    if (!sym) return;
    sym->SizeOfStruct = sizeof(SYMBOL_INFO);
    sym->MaxNameLen = 255;
    for (USHORT i = 0; i < count; ++i) {
        DWORD64 displacement = 0;
        if (SymFromAddr(GetCurrentProcess(), (DWORD64)(uintptr_t)frames[i], &displacement, sym))
            fprintf(stderr, "  #%u %s+0x%llX\n", i, sym->Name, (unsigned long long)displacement);
        else
            fprintf(stderr, "  #%u 0x%llX\n", i, (unsigned long long)(uintptr_t)frames[i]);
    }
    free(sym);
    fflush(stderr);
}

static LONG CALLBACK crash_handler(PEXCEPTION_POINTERS ep)
{
    if (ep->ExceptionRecord->ExceptionCode == EXCEPTION_ACCESS_VIOLATION) {
        if (xbox_VehMmioFault(ep))
            return EXCEPTION_CONTINUE_EXECUTION;
        uintptr_t fault = ep->ExceptionRecord->ExceptionInformation[1];
        fprintf(stderr, "[CRASH] RIP=0x%llX fault=0x%llX (%s)\n",
                (unsigned long long)ep->ContextRecord->Rip,
                (unsigned long long)fault,
                ep->ExceptionRecord->ExceptionInformation[0] ? "write" : "read");
        fprintf(stderr, "  guest eax=%08X ecx=%08X edx=%08X esp=%08X\n",
                g_eax, g_ecx, g_edx, g_esp);
        fprintf(stderr, "  guest ebx=%08X esi=%08X edi=%08X\n", g_ebx, g_esi, g_edi);
        SYMBOL_INFO* sym = (SYMBOL_INFO*)calloc(1, sizeof(SYMBOL_INFO) + 256);
        if (sym) {
            DWORD64 displacement = 0;
            sym->SizeOfStruct = sizeof(SYMBOL_INFO);
            sym->MaxNameLen = 255;
            if (SymFromAddr(GetCurrentProcess(), ep->ContextRecord->Rip, &displacement, sym))
                fprintf(stderr, "  native symbol: %s+0x%llX\n", sym->Name,
                        (unsigned long long)displacement);
            free(sym);
        }
        fflush(stderr);
    }
    return EXCEPTION_CONTINUE_SEARCH;
}

static void default_env(const char* name, const char* value)
{
    if (!getenv(name))
        _putenv_s(name, value);
}

static int fail(const char* message)
{
    fprintf(stderr, "[BOOT] %s\n", message);
    MessageBoxA(NULL, message, "SonicHeroesPC", MB_OK | MB_ICONERROR);
    return 1;
}

int WINAPI WinMain(HINSTANCE instance, HINSTANCE previous, LPSTR command, int show)
{
    wchar_t exe_path[32768], game_dir[MAX_PATH];
    char game_dir_utf8[MAX_PATH * 4];
    void* xbe = NULL;
    size_t xbe_size = 0;
    int result = 1;
    (void)instance; (void)previous; (void)command; (void)show;
    setvbuf(stdout, NULL, _IONBF, 0);
    setvbuf(stderr, NULL, _IONBF, 0);

    /* These OGlue paths are opt-in in the generic runtime. Heroes needs the
     * APU handshake and the pushbuffer executor; enable them unless the user
     * explicitly supplied a value. */
    default_env("RECOMP_AC97_READY", "1");
    default_env("RECOMP_PB_EXEC", "1");
    default_env("GENRY_GPU", "1");
    default_env("RECOMP_STUB_LOG", "1");

    DWORD n = GetModuleFileNameW(NULL, exe_path, (DWORD)(sizeof(exe_path) / sizeof(exe_path[0])));
    if (n == 0 || n >= sizeof(exe_path) / sizeof(exe_path[0]))
        return fail("Cannot locate the executable.");
    wchar_t* slash = wcsrchr(exe_path, L'\\');
    if (!slash) return fail("Cannot locate the game directory.");
    *slash = L'\0';
    if (wcslen(exe_path) > 200)
        return fail("Move the game to a shorter path (the Xbox path layer uses MAX_PATH).");
    if (!SetCurrentDirectoryW(exe_path))
        return fail("Cannot enter the game directory.");
    if (swprintf_s(game_dir, MAX_PATH, L"%s\\game", exe_path) < 0)
        return fail("The game path is too long.");
    if (!WideCharToMultiByte(CP_UTF8, 0, game_dir, -1, game_dir_utf8,
                             sizeof(game_dir_utf8), NULL, NULL))
        return fail("Cannot convert the game path to UTF-8.");

    SymSetOptions(SYMOPT_DEFERRED_LOADS | SYMOPT_UNDNAME);
    SymInitialize(GetCurrentProcess(), NULL, TRUE);
    AddVectoredExceptionHandler(1, crash_handler);

    wchar_t xbe_path[MAX_PATH];
    if (swprintf_s(xbe_path, MAX_PATH, L"%s\\default.xbe", game_dir) < 0)
        return fail("The XBE path is too long.");
    xbe = load_xbe(xbe_path, &xbe_size);
    if (!xbe) return fail("Missing or unreadable game\\default.xbe.");
    if (!xbe_matches(xbe, xbe_size)) {
        free(xbe);
        return fail("game\\default.xbe does not match this recompilation. Use the game folder from the same artifact.");
    }

    heroes_entry_fn entry = recomp_lookup(HEROES_ENTRY_POINT);
    if (!entry) { free(xbe); return fail("The generated dispatch table has no game entry point."); }
    if (!xbox_MemoryLayoutInit(xbe, xbe_size)) {
        free(xbe);
        return fail("Xbox memory layout initialization failed.");
    }
    g_xbox_mem_offset = xbox_GetMemoryOffset();
    g_apu_state = mcpx_apu_init_standalone((uint8_t*)xbox_GetMemoryBase());
    if (!g_apu_state) {
        xbox_MemoryLayoutShutdown();
        free(xbe);
        return fail("APU/audio initialization failed. Check the Windows audio device.");
    }

    /* The fence mirror is what makes two of the Heroes-era runtime fixes live:
     *   - xbox_Nv2aFenceWordVa() names the semaphore the executor writes under
     *     GENRY_HONEST_FENCE (save-card flicker, torn models);
     *   - xbox_Nv2aBusyEventVa() finds D3D's KEVENT at device+0x1DCC, which the
     *     busy-block wait in kernel_bridge.c polls (the NOW LOADING 0 FPS hang).
     * Both return 0 while no device is registered, so say so either way. */
#if HEROES_D3D_DEVICE_PTR != 0
    if (xbox_Nv2aMirrorFence(HEROES_D3D_DEVICE_PTR, HEROES_D3D_PUT_OFF,
                             HEROES_D3D_GET_PTR_OFF) == 0) {
        default_env("GENRY_HONEST_FENCE", "1");
        fprintf(stderr, "[BOOT] fence mirror registered: device ptr 0x%08X\n",
                (unsigned)HEROES_D3D_DEVICE_PTR);
    } else {
        fprintf(stderr, "[BOOT] fence mirror table full; fence fixes stay off\n");
    }
#else
    fprintf(stderr, "[BOOT] no D3D device pointer supplied: honest fence and the "
                    "D3D busy-block wait stay dormant (see docs/SONIC_HEROES_PC_RU.md)\n");
#endif

    /* XDK 5849 numbers some kernel exports differently. The map is generated
     * from this XBE's imports, not copied from another game. */
    xbox_kernel_set_ordinal_remap(g_xbox_ordinal_remap, XBOX_ORDINAL_REMAP_COUNT);
    xbox_kernel_init();
    xbox_path_init(game_dir_utf8, NULL);
    xbox_kernel_bridge_init();
    g_esp = XBOX_STACK_TOP;
    if (!recomp_dispatch_init())
        fprintf(stderr, "[BOOT] flat dispatch unavailable; using binary search\n");
    xbox_WatchdogStart();

    fprintf(stderr, "[BOOT] Sonic Heroes entry 0x%08X; experimental OGlue build\n",
            HEROES_ENTRY_POINT);
    entry();
    result = 0;

    mcpx_apu_shutdown(g_apu_state);
    g_apu_state = NULL;
    xbox_kernel_shutdown();
    xbox_MemoryLayoutShutdown();
    free(xbe);
    SymCleanup(GetCurrentProcess());
    return result;
}

int main(void)
{
    return WinMain(GetModuleHandle(NULL), NULL, GetCommandLineA(), SW_SHOW);
}
