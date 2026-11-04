#pragma once
#include <windows.h>
#include <stdint.h>
#include <string.h>

namespace acv {
struct PatchBytes { void* target; const void* before; const void* after; size_t size; DWORD protection; };
struct PatchCheck { const void* target; const void* expected; size_t size; };
// All other execution must be excluded. A writer failure can occur AFTER copying.
// Always roll back the complete transaction, including the failing destination.
// Destinations must occupy distinct pages; this is not a general overlapping-page writer.
template<class Api> LONG publish_patches(Api& api,PatchBytes* changes,size_t count,
    const PatchCheck* checks=nullptr,size_t check_count=0) {
    for (size_t i=0;i<check_count;++i)
        if (memcmp(checks[i].target,checks[i].expected,checks[i].size)) return -1;
    for (size_t i=0;i<count;++i)
        if (memcmp(changes[i].target,changes[i].before,changes[i].size)) return -1;
    size_t opened = 0;
    for (;opened<count;++opened) {
        auto& c = changes[opened];
        if (!api.protect(c.target,c.size,PAGE_EXECUTE_READWRITE,&c.protection)) break;
    }
    if (opened != count) {
        bool restored = true;
        while (opened) { auto& c = changes[--opened]; DWORD ignored;
            if (!api.protect(c.target,c.size,c.protection,&ignored)) restored = false;
        }
        return restored ? -1 : -2;
    }
    for (size_t i=0;i<count;++i) memcpy(changes[i].target,changes[i].after,changes[i].size);
    bool good = true;
    for (size_t i=0;i<count;++i) {
        auto& c = changes[i]; DWORD ignored;
        if (!api.flush(c.target,c.size)) good = false;
        if (!api.protect(c.target,c.size,c.protection,&ignored)) good = false;
    }
    if (good) return 2;
    // Restore even sites whose protections were already successfully restored.
    bool restored = true;
    for (size_t i=0;i<count;++i) {
        auto& c = changes[i]; DWORD ignored;
        if (!api.protect(c.target,c.size,PAGE_EXECUTE_READWRITE,&ignored)) restored = false;
    }
    if (!restored) return -2; // Do not memcpy into a page whose writability is unknown.
    for (size_t i=0;i<count;++i) memcpy(changes[i].target,changes[i].before,changes[i].size);
    for (size_t i=0;i<count;++i) {
        auto& c = changes[i]; DWORD ignored;
        if (!api.flush(c.target,c.size)) restored = false;
        if (!api.protect(c.target,c.size,c.protection,&ignored)) restored = false;
    }
    return restored ? -1 : -2;
}
struct PatchWindows {
    bool protect(void* p,size_t n,DWORD access,DWORD* old) { return !!VirtualProtect(p,n,access,old); }
    bool flush(void* p,size_t n) { return !!FlushInstructionCache(GetCurrentProcess(),p,n); }
};
}
