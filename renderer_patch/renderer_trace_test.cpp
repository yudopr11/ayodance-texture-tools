// CPU-only diagnostics checks: no D3D device, installed DLL or game process.
#include "d3d9_proxy.cpp"
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>

static HRESULT stub_result;
static unsigned calls;
static HRESULT WINAPI stub_create(IDirect3DDevice9*,const void*,UINT,UINT,UINT,UINT,DWORD,
    D3DFORMAT,D3DPOOL,DWORD,DWORD,D3DCOLOR,ImageInfo*,PALETTEENTRY*,IDirect3DTexture9**) {
    ++calls;
    return stub_result;
}
int main() {
    assert(acv::trace_crc32(nullptr,0) == 0);
    assert(acv::trace_crc32("123456789",9) == 0xcbf43926);
    char directory[MAX_PATH],path[MAX_PATH];
    assert(GetTempPathA(MAX_PATH,directory));
    char marker_directory[MAX_PATH],module_path[MAX_PATH],marker_path[MAX_PATH];
    assert(GetTempFileNameA(directory,"act",0,marker_directory));
    assert(DeleteFileA(marker_directory) && CreateDirectoryA(marker_directory,nullptr));
    snprintf(module_path,sizeof module_path,"%s\\d3d9.dll",marker_directory);
    snprintf(marker_path,sizeof marker_path,"%s\\renderer-trace.enabled",marker_directory);
    assert(SetEnvironmentVariableA("ACV_RENDER_TRACE",nullptr));
    assert(!trace_requested(module_path) && !trace_requested(nullptr));
    assert(SetEnvironmentVariableA("ACV_RENDER_TRACE","1") && trace_requested(module_path));
    assert(SetEnvironmentVariableA("ACV_RENDER_TRACE","0") && !trace_requested(module_path));
    HANDLE marker = CreateFileA(marker_path,GENERIC_WRITE,0,nullptr,CREATE_NEW,0,nullptr);
    assert(marker != INVALID_HANDLE_VALUE && CloseHandle(marker));
    assert(trace_requested(module_path));
    assert(DeleteFileA(marker_path) && !trace_requested(module_path));
    assert(CreateDirectoryA(marker_path,nullptr) && !trace_requested(module_path));
    assert(RemoveDirectoryA(marker_path) && RemoveDirectoryA(marker_directory));
    assert(SetEnvironmentVariableA("ACV_RENDER_TRACE",nullptr));
    assert(GetTempFileNameA(directory,"acv",0,path));
    log_file = CreateFileA(path,GENERIC_READ|GENERIC_WRITE,0,nullptr,OPEN_EXISTING,0,nullptr);
    assert(log_file != INVALID_HANDLE_VALUE);
    DWORD written;
    assert(WriteFile(log_file,"SENTINEL\n",9,&written,nullptr) && written == 9);
    SetFilePointer(log_file,0,nullptr,FILE_BEGIN);
    trace_textures = true;
    original_create = stub_create;
    uint8_t input[128]{};
    memcpy(input,"DDS ",4);
    acv::put32(input+4,124); acv::put32(input+12,4); acv::put32(input+16,4);
    acv::put32(input+76,32); acv::put32(input+80,0x41); acv::put32(input+88,32);
    acv::Tag tag = {{'A','C','V','U','I','2','X',0},1,2,2,4,4};
    memcpy(input+32,&tag,sizeof tag);
    IDirect3DTexture9* texture = nullptr;
    auto invoke = [&](bool handled) {
        return create_tagged(nullptr,input,sizeof input,1,1,1,0,D3DFMT_UNKNOWN,D3DPOOL_MANAGED,
                             3,3,0,nullptr,nullptr,&texture,handled,0xca5aae);
    };
    assert(invoke(true) == D3DERR_INVALIDCALL && calls == 0);
    stub_result = D3DERR_OUTOFVIDEOMEMORY;
    assert(invoke(false) == stub_result && calls == 1);
    trace_textures = false;
    DWORD previous_size = GetFileSize(log_file,nullptr);
    stub_result = S_OK;
    assert(invoke(false) == S_OK && calls == 2);
    assert(GetFileSize(log_file,nullptr) == previous_size);
    assert(FlushFileBuffers(log_file));
    SetFilePointer(log_file,0,nullptr,FILE_BEGIN);
    char contents[16384]{};
    DWORD read;
    assert(ReadFile(log_file,contents,sizeof contents-1,&read,nullptr));
    assert(!strncmp(contents,"SENTINEL\n",9));
    assert(strstr(contents,"Z pid=") && strstr(contents," tid="));
    assert(strstr(contents,"stage=INVALID_ARGUMENT hr=0x8876086c"));
    assert(strstr(contents,"stage=FORWARD_BEGIN") && strstr(contents,"stage=FORWARD_END hr=0x8876017c"));
    assert(strstr(contents,"caller=0x00ca5aae") && strstr(contents,"crc_bytes=128 crc32="));
    CloseHandle(log_file); log_file = INVALID_HANDLE_VALUE;
    assert(DeleteFileA(path));
    puts("PASS: CRC32, structured logging, failure context and unchanged forwarding without a GPU");
}
