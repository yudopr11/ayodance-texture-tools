// Isolated offline-emulator fixture, NOT a renderer DLL; never install into the game.
#include "archive_io_hooks.hpp"
static void log_message(const char*) {}
extern "C" __declspec(dllexport) void __cdecl InitArchiveIoTest(
    uintptr_t allocate,uintptr_t release,uintptr_t read,uintptr_t map,uintptr_t unmap) {
    client_allocate = reinterpret_cast<ClientAllocate>(allocate);
    client_release = reinterpret_cast<ClientRelease>(release);
    original_archive_read = reinterpret_cast<ArchiveRead>(read);
    original_archive_map = reinterpret_cast<ArchiveMap>(map);
    original_archive_unmap = reinterpret_cast<ArchiveUnmap>(unmap);
    install_state = 2; // Test image only: production AcvInstallV64 remains hard-gated.
}
extern "C" __declspec(dllexport) uintptr_t __cdecl ArchiveIoTestThunk(unsigned index) {
    if (index == 0) return uintptr_t(hooked_archive_read);
    if (index == 1) return uintptr_t(hooked_archive_map);
    return index == 2 ? uintptr_t(hooked_archive_scratch) : 0;
}
extern "C" __declspec(dllexport) BOOL WINAPI UnmapArchiveIoTest(LPCVOID base) {
    return hooked_archive_unmap(base);
}
