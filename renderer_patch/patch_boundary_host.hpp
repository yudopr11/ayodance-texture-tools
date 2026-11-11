#pragma once
#include "patch_threads.hpp"
#include "patch_publish.hpp"
#include "archive_cache_patch.hpp"

// Scoped experiment: no prefix views, I/O hooks, scratch hook or archive-phase detours.
struct PatchRequest {
    acv::PatchBytes* changes;
    size_t count;
    acv::NextThread next;
    acv::QueryThread query;
    const acv::PatchCheck* checks;
    size_t check_count;
    volatile LONG* state;
    uintptr_t caller_stack;
    DWORD thread;
    bool acknowledged;
    LONG result;
    const acv::CodeRange* patch_code;
    size_t patch_code_count;
    const uint32_t* cache_count_ptr;
};
static PatchRequest* pending_patch;
static bool commit_scoped_patch(void* context) {
    auto* request = static_cast<PatchRequest*>(context);
    // Recheck under exclusion; pre-graphics sound opens may have populated the cache.
    if (request->cache_count_ptr && *request->cache_count_ptr > 12) return false;
    acv::PatchWindows api;
    request->result = acv::publish_patches(api,request->changes,request->count,request->checks,request->check_count);
    if (request->result == -2) {
        // Never resume application execution with an incoherent instruction stream.
        acv::patch_fail_stop(0xe042ac02u);
    }
    InterlockedExchange(request->state,request->result);
    return request->result == 2;
}
extern "C" __declspec(dllexport) BOOL WINAPI AcvCommitAtLoaderBoundary(HMODULE boundary) {
    auto* request = pending_patch;
    if (!boundary || !request || request->acknowledged || request->thread != GetCurrentThreadId() ||
        *request->state != 1) return FALSE;
    request->acknowledged = true;
    // Start at the installer's caller, excluding its prepared target pointers from the scan.
    auto* tib = reinterpret_cast<NT_TIB*>(NtCurrentTeb());
    if (!acv::stack_outside_code(request->caller_stack,uintptr_t(tib->StackBase),request->patch_code,request->patch_code_count)) return FALSE;
    DWORD error;
    return acv::with_threads_frozen(request->next,request->query,request->patch_code,request->patch_code_count,commit_scoped_patch,request,error);
}
static LONG install_scoped_at_boundary(HMODULE self_module,volatile LONG& state,
    acv::PatchBytes* changes,size_t count,const acv::PatchCheck* checks,size_t check_count,
    uintptr_t caller_stack,const acv::CodeRange* patch_code,size_t patch_code_count,
    const uint32_t* cache_count_ptr) {
    HMODULE native = GetModuleHandleW(L"ntdll.dll");
    auto next = native ? reinterpret_cast<acv::NextThread>(GetProcAddress(native,"NtGetNextThread")) : nullptr;
    auto query = native ? reinterpret_cast<acv::QueryThread>(GetProcAddress(native,"NtQueryInformationThread")) : nullptr;
    wchar_t path[MAX_PATH]{};
    DWORD size = GetModuleFileNameW(self_module,path,MAX_PATH);
    if (!next || !query || !size || size >= MAX_PATH) return -1;
    wchar_t* slash = wcsrchr(path,L'\\');
    const wchar_t suffix[] = L"\\acv_patch_boundary.dll";
    if (!slash || size_t(slash-path)+sizeof suffix/sizeof(wchar_t) > MAX_PATH) return -1;
    memcpy(slash,suffix,sizeof suffix);
    // A previously loaded helper would not execute PROCESS_ATTACH again.
    if (GetModuleHandleW(path)) return -1;
    PatchRequest request{changes,count,next,query,checks,check_count,&state,caller_stack,GetCurrentThreadId(),false,-1,
        patch_code,patch_code_count,cache_count_ptr};
    pending_patch = &request;
    HMODULE helper = LoadLibraryExW(path,nullptr,LOAD_WITH_ALTERED_SEARCH_PATH);
    pending_patch = nullptr;
    if (!request.acknowledged || !helper || request.result != 2) return -1;
    return 2;
}
