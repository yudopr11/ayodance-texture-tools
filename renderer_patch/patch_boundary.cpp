// A loader-lock rendezvous, not an injector. Loaded only by the matching host request.
#include <windows.h>
BOOL WINAPI DllMain(HINSTANCE instance,DWORD reason,void*) {
    if (reason != DLL_PROCESS_ATTACH) return TRUE;
    DisableThreadLibraryCalls(instance);
    HMODULE host = GetModuleHandleW(L"d3d9.dll");
    using Commit = BOOL (WINAPI*)(HMODULE);
    auto commit = host ? reinterpret_cast<Commit>(GetProcAddress(host,"AcvCommitAtLoaderBoundary")) : nullptr;
    return commit && commit(instance);
}
