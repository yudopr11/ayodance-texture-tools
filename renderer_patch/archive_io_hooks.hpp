// Included by the v64 proxy only. Candidate: activation remains behind its startup gate.
#pragma once
#include <windows.h>
#include <string.h>
#include "archive_io.hpp"
#include "archive_file_read.hpp"
using ClientAllocate = void* (__cdecl*)(size_t);
using ClientRelease = void (__cdecl*)(void*);
static ClientAllocate client_allocate;
static ClientRelease client_release;
static volatile LONG install_state;
static void log_message(const char* message);
using ArchiveMap = LPVOID (WINAPI*)(HANDLE,DWORD,DWORD,DWORD,SIZE_T);
using ArchiveUnmap = BOOL (WINAPI*)(LPCVOID);
using ArchiveRead = BOOL (WINAPI*)(HANDLE,LPVOID,DWORD,LPDWORD,LPOVERLAPPED);
static ArchiveMap original_archive_map;
static ArchiveUnmap original_archive_unmap;
static ArchiveRead original_archive_read;
static SRWLOCK archive_views_lock = SRWLOCK_INIT;
static acv::ArchiveViews archive_views(256ull*1024*1024);

static bool archive_memory(const void* pointer,size_t size,bool writable=false) {
    uintptr_t begin = uintptr_t(pointer);
    if (!begin || size > UINTPTR_MAX - begin) return false;
    uintptr_t end = begin+size;
    while (begin < end) {
        MEMORY_BASIC_INFORMATION region{};
        if (!VirtualQuery(reinterpret_cast<void*>(begin),&region,sizeof region) ||
            region.State != MEM_COMMIT || (region.Protect & (PAGE_GUARD|PAGE_NOACCESS))) return false;
        DWORD access = region.Protect & 255;
        if (writable && access != PAGE_READWRITE && access != PAGE_WRITECOPY &&
            access != PAGE_EXECUTE_READWRITE && access != PAGE_EXECUTE_WRITECOPY) return false;
        uintptr_t next = uintptr_t(region.BaseAddress)+region.RegionSize;
        if (next <= begin) return false;
        begin = next;
    }
    return true;
}
struct ArchiveFileRead {
    HANDLE file;
    DWORD error = ERROR_INVALID_DATA;
    static bool read(void* context,uint32_t offset,void* data,uint32_t size,uint32_t* received) {
        auto& f = *static_cast<ArchiveFileRead*>(context);
        return acv::archive_read_at(original_archive_read,f.file,offset,data,size,received,f.error);
    }
    static void* allocate(void* context,size_t bytes) {
        void* result = client_allocate(bytes);
        if (!result) static_cast<ArchiveFileRead*>(context)->error = ERROR_NOT_ENOUGH_MEMORY;
        return result;
    }
    static void release(void*,void* data) { client_release(data); }
};
// Captured frame: return address followed by the original stdcall arguments.
extern "C" BOOL __cdecl archive_read_dispatch(uint32_t* frame,uint32_t owner,void* resource,uint32_t bp) {
    if (frame[0] != 0x005ab427 || install_state != 2)
        return original_archive_read(HANDLE(frame[1]),LPVOID(frame[2]),frame[3],LPDWORD(frame[4]),LPOVERLAPPED(frame[5]));
    auto r = static_cast<uint8_t*>(resource);
    bool resource_ok = archive_memory(r,84,true);
    void** owned = resource_ok ? reinterpret_cast<void**>(r+0x50) : nullptr;
    bool native_frame = bp >= 0x328 && archive_memory(LPVOID(bp-0x328),0x328) &&
        *reinterpret_cast<void**>(bp-0x328) == resource;
    auto fail = [&](DWORD error) {
        if (owned) {
            void* old = *owned; *owned = nullptr;
            if (old && native_frame) client_release(old); // Free the trusted resource's buffer, never the request pointer.
        }
        if (archive_memory(LPVOID(frame[4]),4,true)) *LPDWORD(frame[4]) = 0;
        SetLastError(error); return FALSE;
    };
    if (!owned || owner >= 500 || !native_frame || frame[4] != bp-0x320 ||
        frame[3] != *reinterpret_cast<uint32_t*>(bp-0x318) || frame[5] ||
        *owned != LPVOID(frame[2]) ||
        HANDLE(frame[1]) != reinterpret_cast<HANDLE*>(0x03c2a080)[owner]) return fail(ERROR_INVALID_DATA);
    const char* name = reinterpret_cast<const char*>(bp-0x110);
    if (!memchr(name,0,256)) return fail(ERROR_INVALID_DATA);
    using Present = unsigned char (__attribute__((thiscall)) *)(void*,const char*);
    using Lookup = uint8_t* (__attribute__((thiscall)) *)(void*,const char*);
    // Lookup is lookup-or-create: check membership first, using the same normalized name.
    if (!reinterpret_cast<Present>(0x005aadb0)(LPVOID(0x0119f9d0),name)) return fail(ERROR_INVALID_DATA);
    uint8_t* metadata = reinterpret_cast<Lookup>(0x005a6130)(LPVOID(0x0119f9d0),name);
    if (!archive_memory(metadata,0x48)) return fail(ERROR_INVALID_DATA);
    acv::ArchiveReadEntry entry{
        *reinterpret_cast<uint32_t*>(metadata+8),*reinterpret_cast<uint32_t*>(metadata+12),
        *reinterpret_cast<uint32_t*>(metadata+28),*reinterpret_cast<uint32_t*>(metadata+32)};
    LARGE_INTEGER size{};
    if (!GetFileSizeEx(HANDLE(frame[1]),&size) || size.QuadPart < 0) return fail(ERROR_INVALID_DATA);
    ArchiveFileRead context{HANDLE(frame[1])};
    acv::ArchiveReadOps ops{&context,ArchiveFileRead::allocate,ArchiveFileRead::release,ArchiveFileRead::read};
    uint32_t received = 0;
    bool ok = acv::read_archive_fallback(*owned,LPVOID(frame[2]),frame[3],entry,owner,
        uint64_t(size.QuadPart),ops,received);
    *LPDWORD(frame[4]) = received;
    if (!ok) { log_message("ARCHIVE_READ_FAIL: owned input cleared before native decode"); SetLastError(context.error); }
    return ok ? TRUE : FALSE;
}
struct ArchiveMapRequest {
    HANDLE mapping; DWORD access, high, low;
    static void* create(void* context,uint64_t bytes) {
        auto& request = *static_cast<ArchiveMapRequest*>(context);
        return original_archive_map(request.mapping,request.access,request.high,request.low,SIZE_T(bytes));
    }
};
extern "C" void* __cdecl archive_map_dispatch(uint32_t* frame,uint32_t esi,uint32_t edi) {
    if (install_state != 2 || (frame[0] != 0x005ab37c && frame[0] != 0x005a7ad5))
        return original_archive_map(HANDLE(frame[1]),frame[2],frame[3],frame[4],frame[5]);
    if (frame[0] == 0x005ab37c) { // Existing retained views bypass this call and must stay live.
        (void)esi; SetLastError(ERROR_NOT_ENOUGH_MEMORY); return nullptr;
    }
    uint32_t owner = edi;
    if (owner >= 500 || frame[2] != FILE_MAP_READ || frame[3] || frame[4]) {
        SetLastError(ERROR_INVALID_DATA); return nullptr;
    }
    HANDLE file = reinterpret_cast<HANDLE*>(0x03c2a080)[owner];
    if (HANDLE(frame[1]) != reinterpret_cast<HANDLE*>(0x03c2a850)[owner]) {
        SetLastError(ERROR_INVALID_DATA); return nullptr;
    }
    LARGE_INTEGER size{};
    if (!GetFileSizeEx(file,&size) || size.QuadPart <= 0 || uint64_t(size.QuadPart) > UINT32_MAX) return nullptr;
    ArchiveFileRead context{file}; uint32_t prefix = 0;
    if (!acv::archive_index_extent({&context,ArchiveFileRead::read},uint64_t(size.QuadPart),owner,prefix)) {
        log_message("ARCHIVE_INDEX_FAIL: bounded wire extent rejected"); SetLastError(ERROR_BAD_FORMAT); return nullptr;
    }
    const uint8_t* retained = reinterpret_cast<const uint8_t*>(0x0119f9c8);
    uint64_t bytes = owner == retained[0] || owner == retained[1] ? uint64_t(size.QuadPart) : prefix;
    // Virtual pages are charged, but MapView receives the exact file extent (last page may be partial).
    ArchiveMapRequest request{HANDLE(frame[1]),frame[2],frame[3],frame[4]};
    AcquireSRWLockExclusive(&archive_views_lock);
    void* view = archive_views.map(bytes,&request,ArchiveMapRequest::create);
    ReleaseSRWLockExclusive(&archive_views_lock);
    if (!view) { log_message("ARCHIVE_MAP_FAIL: view budget or OS mapping refused"); SetLastError(ERROR_NOT_ENOUGH_MEMORY); }
    return view;
}
static bool archive_destroy_view(void*,void* base) { return original_archive_unmap(base) != FALSE; }
static BOOL WINAPI hooked_archive_unmap(LPCVOID base) {
    AcquireSRWLockExclusive(&archive_views_lock);
    bool ours = archive_views.owns(const_cast<void*>(base));
    BOOL result = ours ? archive_views.unmap(const_cast<void*>(base),nullptr,archive_destroy_view) :
        original_archive_unmap(base);
    ReleaseSRWLockExclusive(&archive_views_lock);
    return result;
}
extern "C" void* __cdecl archive_scratch_dispatch(uint32_t requested,uint8_t* resource) {
    if (install_state != 2) return client_allocate(requested);
    if (!archive_memory(resource,84)) return nullptr;
    uint32_t raw = *reinterpret_cast<uint32_t*>(resource+12);
    uint32_t stored = *reinterpret_cast<uint32_t*>(resource+16);
    uint32_t size = acv::archive_scratch_bytes(raw,stored);
    if (!size || requested != uint32_t(float(raw)*1.2f)) return nullptr;
    return client_allocate(size);
}
// Capture the native nonvolatile registers and original stack before any C++ prologue.
extern "C" __attribute__((naked)) BOOL WINAPI hooked_archive_read(HANDLE,LPVOID,DWORD,LPDWORD,LPOVERLAPPED) {
    __asm__ volatile("movl %esp,%eax\n\tpushl %ebp\n\tpushl %edi\n\tpushl %esi\n\tpushl %eax\n\t"
        "call _archive_read_dispatch\n\taddl $16,%esp\n\tretl $20\n\t");
}
extern "C" __attribute__((naked)) LPVOID WINAPI hooked_archive_map(HANDLE,DWORD,DWORD,DWORD,SIZE_T) {
    __asm__ volatile("movl %esp,%eax\n\tpushl %edi\n\tpushl %esi\n\tpushl %eax\n\t"
        "call _archive_map_dispatch\n\taddl $12,%esp\n\tretl $20\n\t");
}
extern "C" __attribute__((naked)) void* __cdecl hooked_archive_scratch(size_t) {
    __asm__ volatile("movl 4(%esp),%eax\n\tpushl %edi\n\tpushl %eax\n\t"
        "call _archive_scratch_dispatch\n\taddl $8,%esp\n\tretl\n\t");
}
