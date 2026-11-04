#pragma once
#include <windows.h>
#include <stdint.h>

// Caller MUST hold the loader lock: normal new thread entry is then excluded.
// No heap, logging, module loading or user locks may be used by the callback.
namespace acv {
using NextThread = LONG (NTAPI*)(HANDLE,HANDLE,ACCESS_MASK,ULONG,ULONG,HANDLE*);
using QueryThread = LONG (NTAPI*)(HANDLE,ULONG,void*,ULONG,ULONG*);
struct ThreadBasic { LONG status; NT_TIB* teb; HANDLE process, thread; ULONG affinity; LONG priority, base_priority; };
[[noreturn]] inline void patch_fail_stop(DWORD code) {
    TerminateProcess(GetCurrentProcess(),code);
    // x86 fast-fail: no exception search or application continuation (Windows 8+).
    for (;;) __asm__ volatile("int $0x29" : : "c"(7) : "memory");
}
struct CodeRange { uintptr_t first, last; };
inline bool in_code(uintptr_t pc,const CodeRange* ranges,size_t count) {
    for (size_t i=0;i<count;++i) if (pc >= ranges[i].first && pc < ranges[i].last) return true;
    return false;
}
inline bool stack_outside_code(uintptr_t sp,uintptr_t top,const CodeRange* ranges,size_t count) {
    // Walk to the TEB's StackBase, including protection-split committed regions.
    if ((sp & 3) || (top & 3) || top < sp || top-sp > 16u*1024*1024) return false;
    while (sp < top) {
        MEMORY_BASIC_INFORMATION region{};
        if (!VirtualQuery(reinterpret_cast<void*>(sp),&region,sizeof region) ||
            region.State != MEM_COMMIT || (region.Protect & (PAGE_GUARD|PAGE_NOACCESS)) ||
            !(region.Protect & (PAGE_READONLY|PAGE_READWRITE|PAGE_WRITECOPY|
                               PAGE_EXECUTE_READ|PAGE_EXECUTE_READWRITE|PAGE_EXECUTE_WRITECOPY))) return false;
        uintptr_t end = uintptr_t(region.BaseAddress)+region.RegionSize;
        if (end <= sp) return false;
        if (end > top) end = top;
        for (; sp < end; sp+=4)
            if (in_code(*reinterpret_cast<const uint32_t*>(sp),ranges,count)) return false;
    }
    return true;
}
inline bool thread_outside_code(QueryThread query,HANDLE thread,const CodeRange* ranges,size_t count) {
    CONTEXT context{}; context.ContextFlags = CONTEXT_CONTROL;
    ThreadBasic basic{};
    if (!GetThreadContext(thread,&context) || in_code(context.Eip,ranges,count) ||
        query(thread,0,&basic,sizeof basic,nullptr) < 0 || !basic.teb) return false;
    MEMORY_BASIC_INFORMATION region{};
    if (!VirtualQuery(basic.teb,&region,sizeof region) || region.State != MEM_COMMIT ||
        (region.Protect & (PAGE_GUARD|PAGE_NOACCESS)) ||
        uintptr_t(basic.teb)+sizeof(NT_TIB) > uintptr_t(region.BaseAddress)+region.RegionSize) return false;
    return context.Esp >= uintptr_t(basic.teb->StackLimit) &&
        stack_outside_code(context.Esp,uintptr_t(basic.teb->StackBase),ranges,count);
}
// Refuse rather than patch when enumeration, suspension, context or stack inspection fails.
inline bool with_threads_frozen(NextThread next,QueryThread query,const CodeRange* ranges,size_t count,
    bool (*commit)(void*),void* context,DWORD& error) {
    HANDLE handles[512]{};
    bool suspended[512]{};
    size_t used = 0;
    HANDLE previous = nullptr;
    bool ready = false, result = false;
    error = ERROR_BUSY;
    for (;;) {
        HANDLE thread = nullptr;
        LONG status = next(GetCurrentProcess(),previous,
            THREAD_SUSPEND_RESUME|THREAD_GET_CONTEXT|THREAD_QUERY_INFORMATION,0,0,&thread);
        if (status == LONG(0x8000001au)) { ready = true; break; }
        if (status < 0 || !thread) break;
        if (used == 512) { CloseHandle(thread); break; }
        handles[used++] = thread; previous = thread;
        DWORD id = GetThreadId(thread);
        if (!id) break;
        if (id == GetCurrentThreadId()) continue;
        if (SuspendThread(thread) == DWORD(-1)) break;
        suspended[used-1] = true;
        if (!thread_outside_code(query,thread,ranges,count)) break;
    }
    if (ready) { result = commit(context); error = result ? ERROR_SUCCESS : ERROR_WRITE_FAULT; }
    // Restore precisely the suspend counts we added, including every refusal path.
    for (size_t i=used;i>0;--i) if (suspended[i-1]) {
        if (ResumeThread(handles[i-1]) == DWORD(-1)) {
            // A lost resume is not recoverable by continuing this process.
            patch_fail_stop(ERROR_PROCESS_ABORTED);
        }
    }
    for (size_t i=0;i<used;++i) CloseHandle(handles[i]);
    return result;
}
}
