// Experimental v64-only renderer shim. Install before texture loading, never by late injection.
#include <windows.h>
#include <d3d9.h>
#include <stdio.h>
#include "texture_metadata.hpp"
#include "trace_crc.hpp"
#include "archive_cache_patch.hpp"
#include "archive_io_hooks.hpp"
#include "archive_io_guards.hpp"
#ifdef ACV_SCOPED_CACHE_TEST
#include "patch_boundary_host.hpp"
static constexpr bool scoped_cache_test = true;
#else
static constexpr bool scoped_cache_test = false;
#endif

static_assert(sizeof(void*) == 4, "Build for x86 Windows");
static HMODULE self_module;
static HMODULE real_d3d9;
static HANDLE log_file = INVALID_HANDLE_VALUE;
static const GUID tag_guid = {0xa873069e,0x6206,0x43ef,{0x88,0x48,0x54,0x29,0x43,0x56,0x32,0x58}};
struct ImageInfo { UINT Width, Height, Depth, MipLevels; D3DFORMAT Format; D3DRESOURCETYPE ResourceType; int ImageFileFormat; };
using CreateTexture = HRESULT (WINAPI*)(IDirect3DDevice9*,const void*,UINT,UINT,UINT,UINT,DWORD,
    D3DFORMAT,D3DPOOL,DWORD,DWORD,D3DCOLOR,ImageInfo*,PALETTEENTRY*,IDirect3DTexture9**);
using AlphaQuery = unsigned char (__attribute__((thiscall)) *)(void*,int,int,D3DLOCKED_RECT*);
static CreateTexture original_create;
static AlphaQuery original_alpha;
static bool trace_textures;
static volatile LONG upload_sequence;
static SRWLOCK log_lock = SRWLOCK_INIT;

static void log_message(const char* message) {
    AcquireSRWLockExclusive(&log_lock);
    if (log_file == INVALID_HANDLE_VALUE) {
        char path[MAX_PATH];
        DWORD size = GetModuleFileNameA(self_module, path, MAX_PATH);
        if (!size || size >= MAX_PATH) { ReleaseSRWLockExclusive(&log_lock); return; }
        char* slash = strrchr(path, '\\');
        if (!slash || size_t(slash-path)+sizeof("\\renderer-patch.log") > sizeof path) {
            ReleaseSRWLockExclusive(&log_lock); return;
        }
        strcpy(slash, "\\renderer-patch.log");
        log_file = CreateFileA(path, GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
                              nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    }
    if (log_file != INVALID_HANDLE_VALUE) {
        SYSTEMTIME now{};
        GetSystemTime(&now);
        char line[2048];
        int count = snprintf(line,sizeof line,"%04u-%02u-%02uT%02u:%02u:%02u.%03uZ pid=%lu tid=%lu %s\r\n",
            now.wYear,now.wMonth,now.wDay,now.wHour,now.wMinute,now.wSecond,now.wMilliseconds,
            GetCurrentProcessId(),GetCurrentThreadId(),message);
        if (count > 0 && size_t(count) < sizeof line) {
            DWORD written;
            OVERLAPPED end{};
            end.Offset = end.OffsetHigh = MAXDWORD; // Atomic append even with GENERIC_WRITE for flushing.
            if (WriteFile(log_file,line,DWORD(count),&written,&end) && written == DWORD(count) && trace_textures &&
                !FlushFileBuffers(log_file)) OutputDebugStringA("ACV renderer trace flush failed\n");
        }
    }
    ReleaseSRWLockExclusive(&log_lock);
}
static FARPROC real_export(const char* name) {
    if (!real_d3d9) {
        wchar_t path[MAX_PATH];
        UINT size = GetSystemDirectoryW(path, MAX_PATH);
        if (!size || size + 11 >= MAX_PATH) return nullptr;
        wcscat(path, L"\\d3d9.dll");
        real_d3d9 = LoadLibraryW(path);
    }
    return real_d3d9 ? GetProcAddress(real_d3d9, name) : nullptr;
}
static HRESULT create_tagged(IDirect3DDevice9* device,const void* data,UINT size,
    UINT width,UINT height,UINT mips,DWORD usage,D3DFORMAT format,D3DPOOL pool,
    DWORD filter,DWORD mip_filter,D3DCOLOR color_key,ImageInfo* info,PALETTEENTRY* palette,
    IDirect3DTexture9** texture,bool ordinary_ui,uintptr_t caller=0) {
    acv::Tag tag{};
    if (!acv::parse_tag(data,size,tag))
        return original_create(device,data,size,width,height,mips,usage,format,pool,
                               filter,mip_filter,color_key,info,palette,texture);
    const void* input = data;
    UINT input_size = size;
    D3DFORMAT requested_format = format;
    UINT upload_mips = mips, actual_mips = 0;
    UINT sample_size = size < 4096 ? size : 4096;
    LONG sequence = InterlockedIncrement(&upload_sequence);
    D3DCAPS9 caps{};
    HRESULT caps_hr = E_PENDING;
    D3DSURFACE_DESC desc{};
    auto report = [&](const char* stage,HRESULT hr) {
        if (!trace_textures && SUCCEEDED(hr)) return;
        MEMORYSTATUSEX memory{};
        memory.dwLength = sizeof memory;
        BOOL memory_ok = GlobalMemoryStatusEx(&memory);
        char message[1400];
        auto* header = static_cast<const uint8_t*>(input);
        snprintf(message,sizeof message,
            "UPLOAD id=%ld stage=%s hr=0x%08lx caller=0x%08lx handled=%u "
            "bytes=%u crc_bytes=%u crc32=%08x tag=%u logical=%ux%u physical=%ux%u "
            "pf=%08x fourcc=%08x bits=%u stored_mips=%u request=%ux%u mips=%u "
            "format=%u pool=%u usage=%08lx filter=%08lx mip_filter=%08lx key=%08lx "
            "upload_bytes=%u upload_format=%u upload_mips=%u caps_hr=0x%08lx max=%ux%u "
            "actual=%ux%u actual_format=%u actual_pool=%u actual_mips=%u mem_ok=%u avail_virtual=%llu avail_phys=%llu",
            sequence,stage,static_cast<unsigned long>(hr),static_cast<unsigned long>(caller),unsigned(ordinary_ui),
            input_size,sample_size,acv::trace_crc32(input,sample_size),tag.version,
            tag.logical_width,tag.logical_height,tag.physical_width,tag.physical_height,
            acv::u32(header+80),acv::u32(header+84),acv::u32(header+88),acv::u32(header+28),
            width,height,mips,unsigned(requested_format),unsigned(pool),usage,filter,mip_filter,color_key,
            size,unsigned(format),upload_mips,static_cast<unsigned long>(caps_hr),unsigned(caps.MaxTextureWidth),unsigned(caps.MaxTextureHeight),
            desc.Width,desc.Height,unsigned(desc.Format),unsigned(desc.Pool),actual_mips,unsigned(memory_ok),
            static_cast<unsigned long long>(memory.ullAvailVirtual),static_cast<unsigned long long>(memory.ullAvailPhys));
        log_message(message);
    };
    if (!ordinary_ui) {
        report("FORWARD_BEGIN",S_OK);
        HRESULT result = original_create(device,data,size,width,height,mips,usage,format,pool,
                                        filter,mip_filter,color_key,info,palette,texture);
        report("FORWARD_END",result);
        return result;
    }
    // These native loaders ignore HRESULT and copy dimensions even after a failed upload.
    if (info) { *info = {}; info->Width = tag.logical_width; info->Height = tag.logical_height; }
    if (texture) *texture = nullptr;
    if (!device || !texture || size > 64u*1024*1024) {
        report("INVALID_ARGUMENT",D3DERR_INVALIDCALL); return D3DERR_INVALIDCALL;
    }
    // Zero asks D3DX to generate a full chain; do not invent mips for single-level UI.
    if (!mips && !usage && pool == D3DPOOL_MANAGED && !color_key &&
        !(mip_filter & 0x7c000000u) && acv::u32(static_cast<const uint8_t*>(data)+28) <= 1) upload_mips = 1;
    report("CAPS_BEGIN",S_OK);
    caps_hr = device->GetDeviceCaps(&caps);
    report("CAPS_END",caps_hr);
    if (FAILED(caps_hr)) return D3DERR_NOTAVAILABLE;
    if (tag.physical_width > caps.MaxTextureWidth || tag.physical_height > caps.MaxTextureHeight) {
        report("DIMENSION_LIMIT",D3DERR_NOTAVAILABLE);
        return D3DERR_NOTAVAILABLE;
    }
    ImageInfo temporary{};
    ImageInfo* output_info = &temporary;
    uint8_t* upload = nullptr;
    if (tag.version == 2) {
        size_t needed = acv::upload_size(data,size);
        if (!needed) { report("UPLOAD_SIZE",D3DERR_INVALIDCALL); return D3DERR_INVALIDCALL; }
        upload = static_cast<uint8_t*>(HeapAlloc(GetProcessHeap(),0,needed));
        if (!upload) { report("UPLOAD_ALLOC",E_OUTOFMEMORY); return E_OUTOFMEMORY; }
        if (!acv::prepare_upload(data,size,upload,needed)) {
            HeapFree(GetProcessHeap(),0,upload);
            report("UPLOAD_PREPARE",D3DERR_INVALIDCALL);
            return D3DERR_INVALIDCALL;
        }
        if (acv::u32(static_cast<const uint8_t*>(data)+80) == 0x20) format = D3DFMT_A8R8G8B8;
        data = upload;
        size = UINT(needed);
    }
    report("CREATE_BEGIN",S_OK);
    HRESULT result = original_create(device,data,size,tag.physical_width,tag.physical_height,
        upload_mips,usage,format,pool,filter,mip_filter,color_key,output_info,palette,texture);
    report("CREATE_END",result);
    if (upload) HeapFree(GetProcessHeap(),0,upload);
    if (FAILED(result)) {
        if (*texture) { (*texture)->Release(); *texture = nullptr; }
        return result;
    }
    HRESULT verify = D3DERR_NOTAVAILABLE;
    if (*texture) {
        report("DESC_BEGIN",S_OK);
        verify = (*texture)->GetLevelDesc(0,&desc);
        if (SUCCEEDED(verify)) actual_mips = (*texture)->GetLevelCount();
        report("DESC_END",verify);
        if (SUCCEEDED(verify)) {
            if (desc.Width != tag.physical_width || desc.Height != tag.physical_height ||
                !acv::minimum_pitch(desc.Width,desc.Format) ||
                (upload_mips != mips && actual_mips != upload_mips)) verify = D3DERR_NOTAVAILABLE;
            else {
                report("METADATA_BEGIN",S_OK);
                verify = (*texture)->SetPrivateData(tag_guid,&tag,sizeof tag,0);
                report("METADATA_END",verify);
            }
        }
    }
    if (FAILED(verify)) {
        report("VERIFY_FAILED",verify);
        if (*texture) (*texture)->Release();
        *texture = nullptr;
        return D3DERR_NOTAVAILABLE;
    }
    if (info) { *info = temporary; info->Width = tag.logical_width; info->Height = tag.logical_height; }
    report("UPLOAD_OK",result);
    static LONG first;
    if (InterlockedCompareExchange(&first,1,0) == 0)
        log_message("TAGGED_TEXTURE_OK: native layout metadata with verified high-resolution GPU storage");
    return result;
}
static HRESULT WINAPI hooked_create(IDirect3DDevice9* device,const void* data,UINT size,
    UINT width,UINT height,UINT mips,DWORD usage,D3DFORMAT format,D3DPOOL pool,
    DWORD filter,DWORD mip_filter,D3DCOLOR key,ImageInfo* info,PALETTEENTRY* palette,
    IDirect3DTexture9** texture) {
    auto caller = reinterpret_cast<uintptr_t>(__builtin_return_address(0));
    return create_tagged(device,data,size,width,height,mips,usage,format,pool,filter,
        mip_filter,key,info,palette,texture,caller == 0x00ca5aae || caller == 0x00ca5d10,caller);
}
static unsigned char __attribute__((fastcall)) hooked_alpha(void* object,void*,int x,int y,
                                                            D3DLOCKED_RECT* locked) {
    auto* bytes = static_cast<uint8_t*>(object);
    auto* texture = *reinterpret_cast<IDirect3DTexture9**>(bytes+4);
    acv::Tag tag{};
    DWORD size = sizeof tag;
    if (!texture || FAILED(texture->GetPrivateData(tag_guid,&tag,&size)) || size != sizeof tag)
        return original_alpha(object,x,y,locked);
    if (x < 0 || y < 0 || uint32_t(x) >= tag.logical_width || uint32_t(y) >= tag.logical_height ||
        acv::u32(bytes+8) != tag.logical_width || acv::u32(bytes+12) != tag.logical_height) return 0;
    auto** cached = reinterpret_cast<uint8_t**>(bytes+0x42c);
    if (!*cached) {
        D3DSURFACE_DESC desc{};
        if (!locked || !locked->pBits || locked->Pitch <= 0 || FAILED(texture->GetLevelDesc(0,&desc)) ||
            desc.Width != tag.physical_width || desc.Height != tag.physical_height ||
            !acv::minimum_pitch(desc.Width,desc.Format) ||
            size_t(locked->Pitch) < acv::minimum_pitch(desc.Width,desc.Format)) {
            log_message("ERROR: unsupported tagged alpha-query format, dimensions or lock"); return 0;
        }
        size_t count = size_t(tag.logical_width)*tag.logical_height;
        if (count > 64u*1024*1024 || !client_allocate || !client_release) {
            log_message("ERROR: logical alpha cache size or allocator rejected"); return 0;
        }
        // Client malloc, not throwing operator new; native cleanup uses this same heap.
        auto* mask = static_cast<uint8_t*>(client_allocate(count));
        if (!mask) { log_message("ERROR: logical alpha cache allocation failed"); return 0; }
        if (!acv::build_alpha(mask,tag,static_cast<const uint8_t*>(locked->pBits),
                             locked->Pitch,desc.Format,desc.Width,desc.Height)) {
            client_release(mask);
            log_message("ERROR: tagged alpha cache dimensions or pitch rejected");
            return 0;
        }
        *cached = mask;
    }
    return (*cached)[size_t(y)*tag.logical_width+x];
}
[[maybe_unused]] static bool write_memory(void* destination,const void* bytes,size_t size) {
    DWORD protection;
    if (!VirtualProtect(destination,size,PAGE_EXECUTE_READWRITE,&protection)) return false;
    memcpy(destination,bytes,size);
    FlushInstructionCache(GetCurrentProcess(),destination,size);
    DWORD ignored;
    if (!VirtualProtect(destination,size,protection,&ignored)) log_message("ERROR: could not restore memory protection");
    return true;
}
using MemoryWrite = bool (*)(void*,const void*,size_t);
// The writer must return false only BEFORE modifying its destination.
struct MemoryChange { void* target; const void* before; const void* after; size_t size; };
[[maybe_unused]] static LONG install_v64_writes(MemoryWrite write,const MemoryChange* changes,size_t count) {
    for (size_t i=0;i<count;++i) if (!write(changes[i].target,changes[i].after,changes[i].size)) {
        bool restored = true;
        while (i) { --i; if (!write(changes[i].target,changes[i].before,changes[i].size)) restored = false; }
        return restored ? -1 : -2;
    }
    return 2;
}
static bool trace_requested(const char* module_path) {
    char trace[2]{};
    if (GetEnvironmentVariableA("ACV_RENDER_TRACE",trace,sizeof trace) == 1 && trace[0] == '1') return true;
    char marker[MAX_PATH];
    if (!module_path || strlen(module_path) >= sizeof marker) return false;
    strcpy(marker,module_path);
    char* slash = strrchr(marker,'\\');
    if (!slash || size_t(slash-marker)+sizeof("\\renderer-trace.enabled") > sizeof marker) return false;
    strcpy(slash,"\\renderer-trace.enabled");
    DWORD attributes = GetFileAttributesA(marker);
    return attributes != INVALID_FILE_ATTRIBUTES && !(attributes & FILE_ATTRIBUTE_DIRECTORY);
}
// D3D initialization follows archive setup; no exclusion-and-drain boundary is established.
static constexpr bool native_patch_startup_verified = false;
extern "C" __declspec(dllexport) BOOL WINAPI AcvInstallV64() {
    if (InterlockedCompareExchange(&install_state,1,0) != 0) return install_state == 2;
    char module_path[MAX_PATH]{};
    DWORD module_size = GetModuleFileNameA(self_module,module_path,MAX_PATH);
    trace_textures = trace_requested(module_size && module_size < MAX_PATH ? module_path : nullptr);
    log_message(trace_textures ? "SESSION: renderer diagnostics v1 trace=1" : "SESSION: renderer diagnostics v1 trace=0");
    if (!native_patch_startup_verified && !scoped_cache_test) {
        InterlockedExchange(&install_state,-1);
        log_message("INACTIVE: candidate native patches withheld; archive-loader quiescence is not established");
        return FALSE;
    }
    auto* base = reinterpret_cast<uint8_t*>(GetModuleHandleW(nullptr));
    if (reinterpret_cast<uintptr_t>(base) != 0x00400000) goto unsupported;
    {
        auto* dos = reinterpret_cast<IMAGE_DOS_HEADER*>(base);
        if (dos->e_magic != IMAGE_DOS_SIGNATURE || dos->e_lfanew != 0x158) goto unsupported;
        auto* nt = reinterpret_cast<IMAGE_NT_HEADERS*>(base+dos->e_lfanew);
        if (nt->Signature != IMAGE_NT_SIGNATURE || nt->FileHeader.Machine != IMAGE_FILE_MACHINE_I386 ||
            nt->OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR32_MAGIC ||
            nt->OptionalHeader.SizeOfImage != 0x03fa3000 ||
            nt->OptionalHeader.AddressOfEntryPoint != 0x03d8a058) goto unsupported;
        const uint8_t loader[] = {0x55,0x8b,0xec,0x81,0xec,0x0c,0x01,0,0,0xa1,0xa4,0x10,0xc2,0x03,0x33,0xc5};
        const uint8_t draw[] = {0x55,0x8b,0xec,0x81,0xec,0x84,0,0,0,0xa1,0xa4,0x10,0xc2,0x03,0x33,0xc5};
        const uint8_t alpha[] = {0x55,0x8b,0xec,0x83,0xec,0x2c,0xa1,0xa4,0x10,0xc2,0x03,0x33,0xc5,0x89,0x45,0xfc};
        const uint8_t thunk[] = {0xff,0x25,0x34,0x45,0xd4,0x00};
        const uint8_t release[] = {0xe9,0x63,0x00,0x00,0x00};
        const uint8_t allocate[] = {0x8b,0xff,0x55,0x8b,0xec,0x5d,0xe9,0xe5,0x91,0x01,0x00};
        const uint8_t allocate_body[] = {0x8b,0xff,0x55,0x8b,0xec,0x56,0x8b,0x75,0x08,0x83,0xfe,0xe0,0x77,0x33,0x33,0xc0,
            0x40,0x85,0xf6,0x0f,0x44,0xf0,0xeb,0x14,0xe8,0xf3,0xf3,0xff,0xff,0x85,0xc0,0x74,0x20,0x56,0xe8,0x29,
            0xc1,0xff,0xff,0x59,0x85,0xc0,0x74,0x15,0x56,0x6a,0x00,0xff,0x35,0x6c,0x70,0xcf,0x03,0xff,0x15,0x40,
            0x42,0xd4,0x00,0x85,0xc0,0x74,0xd9,0xeb,0x0d,0xe8,0x1c,0xee,0xfe,0xff,0xc7,0x00,0x0c,0,0,0,0x33,0xc0,0x5e,0x5d,0xc3};
        const uint8_t create_call[] = {0xe8,0xdc,0x9f,0x01,0x00};
        const uint8_t reload_call[] = {0xe8,0x7a,0x9d,0x01,0x00};
        auto* cache_patch = reinterpret_cast<void*>(acv::archive_cache_patch_address);
        // The scoped experiment excludes execution during publication; existing nodes retain their ABI.
        uint32_t cache_count = scoped_cache_test ? 0 : *reinterpret_cast<const uint32_t*>(0x03c2a064);
        if (!acv::archive_cache_ready(cache_count,cache_patch,
            reinterpret_cast<void*>(0x430ca0),reinterpret_cast<void*>(0x430f24),
            reinterpret_cast<void*>(0xcbfb68))) goto unsupported;
        if (memcmp(reinterpret_cast<void*>(0xccb6f0),allocate,sizeof allocate) ||
            memcmp(reinterpret_cast<void*>(0xce48e0),allocate_body,sizeof allocate_body) ||
            memcmp(reinterpret_cast<void*>(0xca5aa9),create_call,sizeof create_call) ||
            memcmp(reinterpret_cast<void*>(0xca5d0b),reload_call,sizeof reload_call) ||
            memcmp(reinterpret_cast<void*>(0xcbfb76),release,sizeof release) ||
            memcmp(reinterpret_cast<void*>(0xca2aa0),loader,sizeof loader) ||
            memcmp(reinterpret_cast<void*>(0x5377f0),draw,sizeof draw) ||
            memcmp(reinterpret_cast<void*>(0xca2940),alpha,sizeof alpha) ||
            memcmp(reinterpret_cast<void*>(0xcbfa8a),thunk,sizeof thunk)) goto unsupported;
        auto* map_slot = reinterpret_cast<ArchiveMap*>(0x00d4414c);
        auto* unmap_slot = reinterpret_cast<ArchiveUnmap*>(0x00d44150);
        auto* read_slot = reinterpret_cast<ArchiveRead*>(0x00d4413c);
        if (!scoped_cache_test) {
            if (!acv::archive_io_ready(base,0x00400000) || archive_views.charged) goto unsupported;
            for (unsigned i=0;i<500;++i)
                if (reinterpret_cast<void**>(0x03c2b020)[i]) goto unsupported;
            HMODULE kernel = GetModuleHandleA("kernel32.dll");
            original_archive_map = kernel ? reinterpret_cast<ArchiveMap>(GetProcAddress(kernel,"MapViewOfFile")) : nullptr;
            original_archive_unmap = kernel ? reinterpret_cast<ArchiveUnmap>(GetProcAddress(kernel,"UnmapViewOfFile")) : nullptr;
            original_archive_read = kernel ? reinterpret_cast<ArchiveRead>(GetProcAddress(kernel,"ReadFile")) : nullptr;
            if (!original_archive_map || !original_archive_unmap || !original_archive_read ||
                *map_slot != original_archive_map || *unmap_slot != original_archive_unmap ||
                *read_slot != original_archive_read) goto unsupported;
        }
        HMODULE d3dx = GetModuleHandleA("d3dx9_43.dll");
        original_create = d3dx ? reinterpret_cast<CreateTexture>(GetProcAddress(d3dx,"D3DXCreateTextureFromFileInMemoryEx")) : nullptr;
        auto* slot = reinterpret_cast<CreateTexture*>(0xd44534);
        if (!original_create || *slot != original_create) goto unsupported;
        HMODULE pinned;
        if (!GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_PIN,
            reinterpret_cast<LPCSTR>(&AcvInstallV64),&pinned)) goto unsupported;
        auto* trampoline = static_cast<uint8_t*>(VirtualAlloc(nullptr,11,MEM_RESERVE|MEM_COMMIT,PAGE_READWRITE));
        if (!trampoline) goto unsupported;
        memcpy(trampoline,alpha,6);
        trampoline[6] = 0xe9;
        uint32_t back = 0xca2946 - (reinterpret_cast<uintptr_t>(trampoline)+11);
        memcpy(trampoline+7,&back,4);
        DWORD ignored;
        if (!VirtualProtect(trampoline,11,PAGE_EXECUTE_READ,&ignored)) {
            VirtualFree(trampoline,0,MEM_RELEASE); goto unsupported;
        }
        if (!FlushInstructionCache(GetCurrentProcess(),trampoline,11)) {
            VirtualFree(trampoline,0,MEM_RELEASE); goto unsupported;
        }
        original_alpha = reinterpret_cast<AlphaQuery>(trampoline);
        client_allocate = reinterpret_cast<ClientAllocate>(0x00ccb6f0);
        client_release = reinterpret_cast<ClientRelease>(0x00cbfb76);
        uint8_t jump[6] = {0xe9,0,0,0,0,0x90};
        uint32_t target = reinterpret_cast<uintptr_t>(&hooked_alpha)-0xca2945;
        memcpy(jump+1,&target,4);
        // Retain the trampoline after any write attempt: rollback must never leave a dangling code target.
        CreateTexture create_replacement = hooked_create;
        ArchiveMap map_replacement = hooked_archive_map;
        ArchiveUnmap unmap_replacement = hooked_archive_unmap;
        ArchiveRead read_replacement = hooked_archive_read;
        const uint8_t scratch_before[] = {0xe8,0xe6,0xd1,0xe8,0xff};
        uint8_t scratch_after[5] = {0xe8,0,0,0,0};
        uint32_t scratch_target = uintptr_t(&hooked_archive_scratch)-0x005ab70a;
        memcpy(scratch_after+1,&scratch_target,4);
        [[maybe_unused]] const MemoryChange changes[] = {
            {reinterpret_cast<void*>(0xca2940),alpha,jump,6},
            {slot,&original_create,&create_replacement,sizeof create_replacement},
            {cache_patch,acv::archive_cache_before,acv::archive_cache_after,sizeof acv::archive_cache_before},
            {read_slot,&original_archive_read,&read_replacement,sizeof read_replacement},
            {map_slot,&original_archive_map,&map_replacement,sizeof map_replacement},
            {unmap_slot,&original_archive_unmap,&unmap_replacement,sizeof unmap_replacement},
            {reinterpret_cast<void*>(0x005ab705),scratch_before,scratch_after,sizeof scratch_after}};
#ifdef ACV_SCOPED_CACHE_TEST
        acv::PatchBytes scoped[] = {
            {reinterpret_cast<void*>(0xca2940),alpha,jump,6,0},
            {slot,&original_create,&create_replacement,sizeof create_replacement,0},
            {cache_patch,acv::archive_cache_before,acv::archive_cache_after,sizeof acv::archive_cache_before,0}};
        const acv::PatchCheck dependencies[] = {
            {reinterpret_cast<void*>(0xccb6f0),allocate,sizeof allocate},
            {reinterpret_cast<void*>(0xce48e0),allocate_body,sizeof allocate_body},
            {reinterpret_cast<void*>(0xcbfb76),release,sizeof release},
            {reinterpret_cast<void*>(0xca5aa9),create_call,sizeof create_call},
            {reinterpret_cast<void*>(0xca5d0b),reload_call,sizeof reload_call},
            {reinterpret_cast<void*>(0xca2aa0),loader,sizeof loader},
            {reinterpret_cast<void*>(0x5377f0),draw,sizeof draw},
            {reinterpret_cast<void*>(0xca2940),alpha,sizeof alpha},
            {reinterpret_cast<void*>(0xcbfa8a),thunk,sizeof thunk},
            {reinterpret_cast<void*>(0x430ca0),acv::archive_erase_entry,sizeof acv::archive_erase_entry},
            {reinterpret_cast<void*>(0x430f24),acv::archive_erase_exit,sizeof acv::archive_erase_exit},
            {reinterpret_cast<void*>(0xcbfb68),acv::archive_sized_release,sizeof acv::archive_sized_release}};
        uintptr_t caller_stack = uintptr_t(__builtin_frame_address(0))+sizeof(void*);
        LONG written = install_scoped_at_boundary(self_module,install_state,scoped,3,
            dependencies,sizeof dependencies/sizeof dependencies[0],caller_stack);
#else
        LONG written = install_v64_writes(write_memory,changes,sizeof changes/sizeof changes[0]);
#endif
        if (written == -2) {
            InterlockedExchange(&install_state,-2);
            log_message("FATAL_PARTIAL_INSTALL: native patch rollback failed; stop the pilot process");
            return FALSE;
        }
        if (written != 2) goto unsupported;
        InterlockedExchange(&install_state,2);
        log_message("ACTIVE_V64: tagged-only texture dimensions and ABI-preserving alpha query enabled");
        if (scoped_cache_test) {
            log_message("CACHE_TEST_V1: full-width eviction IDs; loader-boundary publication; archive I/O unchanged");
        } else {
            log_message("ARCHIVE_CACHE_FIX: full-width eviction IDs enabled; startup cache empty");
            log_message("ARCHIVE_IO: bounded index views, 256 MiB page budget, exact stored-byte resource reads");
        }
        return TRUE;
    }
unsupported:
    InterlockedExchange(&install_state,-1);
    log_message("INACTIVE: recovered v64 signatures/imports not present; no renderer fix enabled");
    return FALSE;
}
extern "C" IDirect3D9* WINAPI Direct3DCreate9(UINT version) {
    using Function = IDirect3D9* (WINAPI*)(UINT);
    auto function = reinterpret_cast<Function>(real_export("Direct3DCreate9"));
    if (!function) return nullptr;
    auto* result = function(version);
    if (result && !AcvInstallV64() && install_state == -2) {
        result->Release();
        return nullptr;
    }
    return result;
}
extern "C" HRESULT WINAPI Direct3DCreate9Ex(UINT version,IDirect3D9Ex** output) {
    using Function = HRESULT (WINAPI*)(UINT,IDirect3D9Ex**);
    auto function = reinterpret_cast<Function>(real_export("Direct3DCreate9Ex"));
    if (!function) return E_NOTIMPL;
    HRESULT result = function(version,output);
    if (SUCCEEDED(result) && !AcvInstallV64() && install_state == -2) {
        if (output && *output) { (*output)->Release(); *output = nullptr; }
        return E_FAIL;
    }
    return result;
}
#define FORWARD_RESULT(type,name,args,values,fallback) \
extern "C" type WINAPI name args { using Function = type (WINAPI*) args; \
    auto function = reinterpret_cast<Function>(real_export(#name)); return function ? function values : fallback; }
#define FORWARD_VOID(name,args,values) \
extern "C" void WINAPI name args { using Function = void (WINAPI*) args; \
    auto function = reinterpret_cast<Function>(real_export(#name)); if (function) function values; }
FORWARD_RESULT(int,D3DPERF_BeginEvent,(D3DCOLOR color,LPCWSTR name),(color,name),-1)
FORWARD_RESULT(int,D3DPERF_EndEvent,(),(),-1)
FORWARD_RESULT(DWORD,D3DPERF_GetStatus,(),(),0)
FORWARD_RESULT(BOOL,D3DPERF_QueryRepeatFrame,(),(),FALSE)
FORWARD_VOID(D3DPERF_SetMarker,(D3DCOLOR color,LPCWSTR name),(color,name))
FORWARD_VOID(D3DPERF_SetRegion,(D3DCOLOR color,LPCWSTR name),(color,name))
FORWARD_VOID(D3DPERF_SetOptions,(DWORD options),(options))
BOOL WINAPI DllMain(HINSTANCE instance,DWORD reason,void*) {
    if (reason == DLL_PROCESS_ATTACH) { self_module = instance; DisableThreadLibraryCalls(instance); }
    return TRUE;
}
