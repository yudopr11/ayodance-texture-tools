// Experimental v64-only renderer shim. Install before texture loading, never by late injection.
#include <windows.h>
#include <d3d9.h>
#include <stdio.h>
#include "texture_metadata.hpp"

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
static volatile LONG install_state;

static void log_message(const char* message) {
    if (log_file == INVALID_HANDLE_VALUE) {
        char path[MAX_PATH];
        DWORD size = GetModuleFileNameA(self_module, path, MAX_PATH);
        if (!size || size >= MAX_PATH) return;
        char* slash = strrchr(path, '\\');
        if (!slash || size_t(slash-path)+sizeof("\\renderer-patch.log") > sizeof path) return;
        strcpy(slash, "\\renderer-patch.log");
        log_file = CreateFileA(path, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE,
                              nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    }
    if (log_file != INVALID_HANDLE_VALUE) {
        DWORD written;
        WriteFile(log_file, message, DWORD(strlen(message)), &written, nullptr);
        WriteFile(log_file, "\r\n", 2, &written, nullptr);
    }
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
    IDirect3DTexture9** texture,bool ordinary_ui) {
    acv::Tag tag{};
    if (!ordinary_ui || !acv::parse_tag(data,size,tag))
        return original_create(device,data,size,width,height,mips,usage,format,pool,
                               filter,mip_filter,color_key,info,palette,texture);
    if (!device || !texture) return D3DERR_INVALIDCALL;
    *texture = nullptr;
    D3DCAPS9 caps{};
    if (FAILED(device->GetDeviceCaps(&caps)) || tag.physical_width > caps.MaxTextureWidth ||
        tag.physical_height > caps.MaxTextureHeight) {
        log_message("ERROR: tagged texture exceeds device limits; not silently downscaling");
        return D3DERR_NOTAVAILABLE;
    }
    ImageInfo temporary{};
    ImageInfo* output_info = info ? info : &temporary;
    uint8_t* upload = nullptr;
    if (tag.version == 2) {
        size_t needed = acv::upload_size(data,size);
        if (!needed) return D3DERR_INVALIDCALL;
        upload = static_cast<uint8_t*>(HeapAlloc(GetProcessHeap(),0,needed));
        if (!upload) return E_OUTOFMEMORY;
        if (!acv::prepare_upload(data,size,upload,needed)) {
            HeapFree(GetProcessHeap(),0,upload);
            return D3DERR_INVALIDCALL;
        }
        if (acv::u32(static_cast<const uint8_t*>(data)+80) == 0x20) format = D3DFMT_A8R8G8B8;
        data = upload;
        size = UINT(needed);
    }
    HRESULT result = original_create(device,data,size,tag.physical_width,tag.physical_height,
        mips,usage,format,pool,filter,mip_filter,color_key,output_info,palette,texture);
    if (upload) HeapFree(GetProcessHeap(),0,upload);
    if (FAILED(result)) { log_message("ERROR: tagged GPU texture creation failed"); return result; }
    D3DSURFACE_DESC desc{};
    if (!*texture || FAILED((*texture)->GetLevelDesc(0,&desc)) ||
        desc.Width != tag.physical_width || desc.Height != tag.physical_height ||
        !acv::minimum_pitch(desc.Width,desc.Format) ||
        FAILED((*texture)->SetPrivateData(tag_guid,&tag,sizeof tag,0))) {
        if (*texture) (*texture)->Release();
        *texture = nullptr;
        log_message("ERROR: physical dimensions or texture metadata verification failed");
        return D3DERR_NOTAVAILABLE;
    }
    output_info->Width = tag.logical_width;
    output_info->Height = tag.logical_height;
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
        mip_filter,key,info,palette,texture,caller == 0x00ca5aae || caller == 0x00ca5d10);
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
        using Allocate = void* (__cdecl*)(size_t);
        auto allocate = reinterpret_cast<Allocate>(0x004388f0);
        // Allocate through the client so its destructor can free this cache normally.
        auto* mask = static_cast<uint8_t*>(allocate(size_t(tag.logical_width)*tag.logical_height));
        if (!mask) { log_message("ERROR: logical alpha cache allocation failed"); return 0; }
        if (!acv::build_alpha(mask,tag,static_cast<const uint8_t*>(locked->pBits),
                             locked->Pitch,desc.Format,desc.Width,desc.Height)) {
            using Release = void (__cdecl*)(void*);
            reinterpret_cast<Release>(0x00cbfb76)(mask);
            log_message("ERROR: tagged alpha cache dimensions or pitch rejected");
            return 0;
        }
        *cached = mask;
    }
    return (*cached)[size_t(y)*tag.logical_width+x];
}
static bool write_memory(void* destination,const void* bytes,size_t size) {
    DWORD protection;
    if (!VirtualProtect(destination,size,PAGE_EXECUTE_READWRITE,&protection)) return false;
    memcpy(destination,bytes,size);
    FlushInstructionCache(GetCurrentProcess(),destination,size);
    DWORD ignored;
    if (!VirtualProtect(destination,size,protection,&ignored)) log_message("ERROR: could not restore memory protection");
    return true;
}
extern "C" __declspec(dllexport) BOOL WINAPI AcvInstallV64() {
    if (InterlockedCompareExchange(&install_state,1,0) != 0) return install_state == 2;
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
        const uint8_t allocate[] = {0x55,0x8b,0xec,0x5d,0xe9,0x3f,0x72,0x88,0x00};
        const uint8_t create_call[] = {0xe8,0xdc,0x9f,0x01,0x00};
        const uint8_t reload_call[] = {0xe8,0x7a,0x9d,0x01,0x00};
        if (memcmp(reinterpret_cast<void*>(0x4388f0),allocate,sizeof allocate) ||
            memcmp(reinterpret_cast<void*>(0xca5aa9),create_call,sizeof create_call) ||
            memcmp(reinterpret_cast<void*>(0xca5d0b),reload_call,sizeof reload_call) ||
            memcmp(reinterpret_cast<void*>(0xcbfb76),release,sizeof release) ||
            memcmp(reinterpret_cast<void*>(0xca2aa0),loader,sizeof loader) ||
            memcmp(reinterpret_cast<void*>(0x5377f0),draw,sizeof draw) ||
            memcmp(reinterpret_cast<void*>(0xca2940),alpha,sizeof alpha) ||
            memcmp(reinterpret_cast<void*>(0xcbfa8a),thunk,sizeof thunk)) goto unsupported;
        HMODULE d3dx = GetModuleHandleA("d3dx9_43.dll");
        original_create = d3dx ? reinterpret_cast<CreateTexture>(GetProcAddress(d3dx,"D3DXCreateTextureFromFileInMemoryEx")) : nullptr;
        auto* slot = reinterpret_cast<CreateTexture*>(0xd44534);
        if (!original_create || *slot != original_create) goto unsupported;
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
        FlushInstructionCache(GetCurrentProcess(),trampoline,11);
        original_alpha = reinterpret_cast<AlphaQuery>(trampoline);
        uint8_t jump[6] = {0xe9,0,0,0,0,0x90};
        uint32_t target = reinterpret_cast<uintptr_t>(&hooked_alpha)-0xca2945;
        memcpy(jump+1,&target,4);
        if (!write_memory(reinterpret_cast<void*>(0xca2940),jump,sizeof jump)) {
            VirtualFree(trampoline,0,MEM_RELEASE); goto unsupported;
        }
        CreateTexture replacement = &hooked_create;
        if (!write_memory(slot,&replacement,sizeof replacement)) {
            // Keep the trampoline allocated even after rollback: no dangling code targets.
            if (!write_memory(reinterpret_cast<void*>(0xca2940),alpha,6)) {
                InterlockedExchange(&install_state,-2);
                log_message("FATAL_PARTIAL_INSTALL: alpha rollback failed; stop the pilot process");
                return FALSE;
            }
            goto unsupported;
        }
        InterlockedExchange(&install_state,2);
        log_message("ACTIVE_V64: tagged-only texture dimensions and ABI-preserving alpha query enabled");
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
