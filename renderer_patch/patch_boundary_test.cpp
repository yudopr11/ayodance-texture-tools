// CPU-only synthetic host/helper. Never loads the client or creates a D3D device.
#include <windows.h>
#include <stdint.h>
#include "patch_threads.hpp"
#include "patch_publish.hpp"
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>

#ifdef ACV_BOUNDARY_TEST_HOST
static acv::NextThread real_next;
static acv::QueryThread real_query;
static HANDLE ready_event, stop_event, child_thread;
static volatile LONG committed, child_entries, callback_active, callbacks;
static volatile LONG* values;
static DWORD observer_id;
static unsigned mode;
static bool fail_next;
static bool must_not_commit(void*) { assert(false); return false; }
static LONG NTAPI fail_after_observer(HANDLE process,HANDLE previous,ACCESS_MASK access,
    ULONG attributes,ULONG flags,HANDLE* next) {
    if (fail_next) return LONG(0xc0000022u);
    LONG status = real_next(process,previous,access,attributes,flags,next);
    if (status >= 0 && GetThreadId(*next) == observer_id) fail_next = true;
    return status;
}
static DWORD WINAPI observer(void*) {
    SetEvent(ready_event);
    while (WaitForSingleObject(stop_event,0) == WAIT_TIMEOUT) {
        if (InterlockedCompareExchange(&committed,0,0))
            for (unsigned i=0;i<3;++i) assert(values[i] == LONG(100+i));
    }
    return 0;
}
static DWORD WINAPI new_entry(void*) {
    assert(!InterlockedCompareExchange(&callback_active,0,0));
    InterlockedIncrement(&child_entries);
    return 0;
}
static bool publish(void*) {
    const LONG before[] = {0,1,2}, after[] = {100,101,102};
    // Synthetic values share a page: only one protection transition is needed.
    acv::PatchBytes whole{const_cast<LONG*>(values),before,after,sizeof before,0};
    acv::PatchWindows api;
    assert(acv::publish_patches(api,&whole,1) == 2);
    InterlockedExchange(&committed,1);
    return true;
}
extern "C" __declspec(dllexport) BOOL WINAPI AcvCommitAtLoaderBoundary(HMODULE module) {
    assert(module);
    InterlockedIncrement(&callbacks);
    DWORD error;
    if (mode == 1) {
        fail_next = false;
        assert(!acv::with_threads_frozen(fail_after_observer,real_query,nullptr,0,must_not_commit,nullptr,error));
        assert(fail_next && error == ERROR_BUSY);
        return FALSE;
    }
    if (mode == 2) {
        // A pending return from WaitForSingleObject into the worker is a refusal witness.
        acv::CodeRange caller{uintptr_t(&observer),uintptr_t(&observer)+256};
        assert(!acv::with_threads_frozen(real_next,real_query,&caller,1,must_not_commit,nullptr,error));
        return FALSE;
    }
    InterlockedExchange(&callback_active,1);
    // This ordinary new thread exists, but cannot enter application code under the loader lock.
    child_thread = CreateThread(nullptr,0,new_entry,nullptr,0,nullptr);
    assert(child_thread);
    assert(acv::with_threads_frozen(real_next,real_query,nullptr,0,publish,nullptr,error));
    assert(error == ERROR_SUCCESS && !child_entries);
    assert(WaitForSingleObject(child_thread,25) == WAIT_TIMEOUT);
    InterlockedExchange(&callback_active,0);
    return TRUE;
}
struct MockApi {
    unsigned calls = 0;
    uint64_t failures;
    bool step() { return !(failures & (uint64_t(1) << ++calls)); }
    bool protect(void*,size_t,DWORD,DWORD* old) { *old = PAGE_READONLY; return step(); }
    bool flush(void*,size_t) { return step(); }
};
static void publication_failures() {
    // Inject every publication/protection/flush position, then each rollback position.
    for (unsigned first=0;first<=9;++first) for (unsigned second=0;second<=9;++second) {
        if (second && first < 4) continue; // No bytes copied on an opening-protection failure.
        LONG targets[] = {0,1,2}, before[] = {0,1,2}, after[] = {100,101,102};
        acv::PatchBytes changes[3];
        for (unsigned i=0;i<3;++i) changes[i] = {targets+i,before+i,after+i,4,0};
        MockApi api{0,(first ? uint64_t(1)<<first : 0) |
            (second ? uint64_t(1)<<(9+second) : 0)};
        LONG result = acv::publish_patches(api,changes,3);
        if (!first) assert(result == 2 && !memcmp(targets,after,sizeof after));
        else if (second) assert(result == -2);
        else assert(result == -1 && !memcmp(targets,before,sizeof before));
    }
    LONG target = 9, before = 0, after = 1;
    acv::PatchBytes c{&target,&before,&after,4,0}; MockApi api{0,0};
    assert(acv::publish_patches(api,&c,1) == -1 && !api.calls && target == 9);
    target = 0;
    const LONG expected_dependency = 10; LONG dependency = 11;
    acv::PatchCheck check{&dependency,&expected_dependency,sizeof dependency};
    assert(acv::publish_patches(api,&c,1,&check,1) == -1 && !api.calls && target == 0);
}
static LONG NTAPI failed_enumeration(HANDLE,HANDLE,ACCESS_MASK,ULONG,ULONG,HANDLE*) { return LONG(0xc0000022u); }
extern "C" __declspec(dllexport) BOOL WINAPI RunBoundaryTest(const wchar_t* helper_path) {
    publication_failures();
    real_next = reinterpret_cast<acv::NextThread>(GetProcAddress(GetModuleHandleW(L"ntdll.dll"),"NtGetNextThread"));
    real_query = reinterpret_cast<acv::QueryThread>(GetProcAddress(GetModuleHandleW(L"ntdll.dll"),"NtQueryInformationThread"));
    assert(real_next && real_query);
    DWORD error;
    assert(!acv::with_threads_frozen(failed_enumeration,real_query,nullptr,0,must_not_commit,nullptr,error));
    values = static_cast<LONG*>(VirtualAlloc(nullptr,4096,MEM_RESERVE|MEM_COMMIT,PAGE_READWRITE));
    assert(values);
    values[0]=0; values[1]=1; values[2]=2;
    // Exact stack/return-witness scan, including a witness at the last readable DWORD.
    acv::CodeRange witness{0x11223340,0x11223350};
    assert(acv::stack_outside_code(uintptr_t(values),uintptr_t(values)+4096,&witness,1));
    values[1023] = LONG(witness.first);
    assert(!acv::stack_outside_code(uintptr_t(values),uintptr_t(values)+4096,&witness,1));
    values[1023] = 0;
    auto* split = static_cast<uint32_t*>(VirtualAlloc(nullptr,8192,MEM_RESERVE|MEM_COMMIT,PAGE_READWRITE));
    assert(split); split[2047] = uint32_t(witness.first);
    DWORD old;
    assert(VirtualProtect(split+1024,4096,PAGE_READONLY,&old));
    assert(!acv::stack_outside_code(uintptr_t(split),uintptr_t(split)+8192,&witness,1));
    assert(VirtualProtect(split+1024,4096,PAGE_NOACCESS,&old));
    assert(!acv::stack_outside_code(uintptr_t(split),uintptr_t(split)+8192,nullptr,0));
    VirtualFree(split,0,MEM_RELEASE);
    ready_event = CreateEventW(nullptr,TRUE,FALSE,nullptr);
    stop_event = CreateEventW(nullptr,TRUE,FALSE,nullptr);
    assert(ready_event && stop_event);
    HANDLE old_thread = CreateThread(nullptr,0,observer,nullptr,0,&observer_id);
    assert(old_thread && WaitForSingleObject(ready_event,5000) == WAIT_OBJECT_0);
    HMODULE helper = LoadLibraryW(helper_path);
    assert(helper && callbacks == 1 && committed == 1);
    assert(WaitForSingleObject(child_thread,5000) == WAIT_OBJECT_0 && child_entries == 1);
    // Already loaded helpers do not rerun attach: a production request must require an ACK.
    HMODULE again = LoadLibraryW(helper_path);
    assert(again == helper && callbacks == 1);
    FreeLibrary(again); FreeLibrary(helper);
    // Failure AFTER suspension must preserve a pre-existing suspend count exactly.
    assert(SuspendThread(old_thread) == 0);
    mode = 1;
    assert(!LoadLibraryW(helper_path) && callbacks == 2);
    assert(ResumeThread(old_thread) == 1);
    mode = 2;
    assert(!LoadLibraryW(helper_path) && callbacks == 3);
    assert(SuspendThread(old_thread) == 0 && ResumeThread(old_thread) == 1);
    SetEvent(stop_event);
    assert(WaitForSingleObject(old_thread,5000) == WAIT_OBJECT_0);
    CloseHandle(old_thread); CloseHandle(child_thread); CloseHandle(ready_event); CloseHandle(stop_event);
    VirtualFree(const_cast<LONG*>(values),0,MEM_RELEASE);
    return TRUE;
}
#else
#include <stdio.h>
int wmain(int argc,wchar_t** argv) {
    assert(argc == 3);
    HMODULE host = LoadLibraryW(argv[1]); assert(host);
    using Run = BOOL (WINAPI*)(const wchar_t*);
    auto run = reinterpret_cast<Run>(GetProcAddress(host,"RunBoundaryTest")); assert(run);
    assert(run(argv[2]));
    FreeLibrary(host);
    puts("Loader-boundary CPU tests: PASS (existing/new threads, publication, failures, stack witnesses)");
}
#endif
