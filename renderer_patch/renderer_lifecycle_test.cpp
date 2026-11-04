// CPU-only COM ABI/lifetime harness; no game addresses, device or GPU are used.
#include <windows.h>
static bool fail_upload_allocation;
static void* WINAPI upload_allocate(HANDLE heap,DWORD flags,SIZE_T size) {
    return fail_upload_allocation ? nullptr : HeapAlloc(heap,flags,size);
}
#define HeapAlloc upload_allocate
#include "d3d9_proxy.cpp"
#undef HeapAlloc
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>
#include <stdlib.h>

static HRESULT caps_result, create_result, desc_result, metadata_result;
static UINT received_mips, returned_mips, create_calls, releases, allocations, frees;
static bool partial_failure, fail_mask, has_tag;
static acv::Tag attached;
static D3DSURFACE_DESC surface;
static void* device_table[8];
static void* texture_table[22];
struct Mock { void** table; } mock_device{device_table}, mock_texture{texture_table};
static HRESULT WINAPI get_caps(IDirect3DDevice9*,D3DCAPS9* caps) {
    caps->MaxTextureWidth = caps->MaxTextureHeight = 16384;
    return caps_result;
}
static ULONG WINAPI release_texture(IDirect3DTexture9*) { ++releases; return 0; }
static HRESULT WINAPI set_tag(IDirect3DTexture9*,REFGUID,const void* data,DWORD size,DWORD flags) {
    assert(size == sizeof attached && !flags);
    if (SUCCEEDED(metadata_result)) { memcpy(&attached,data,size); has_tag = true; }
    return metadata_result;
}
static HRESULT WINAPI get_tag(IDirect3DTexture9*,REFGUID,void* data,DWORD* size) {
    if (!has_tag) return D3DERR_NOTFOUND;
    assert(*size >= sizeof attached); memcpy(data,&attached,sizeof attached); *size = sizeof attached;
    return S_OK;
}
static UINT WINAPI level_count(IDirect3DTexture9*) { return returned_mips; }
static HRESULT WINAPI level_desc(IDirect3DTexture9*,UINT level,D3DSURFACE_DESC* desc) {
    assert(!level); *desc = surface; return desc_result;
}
static HRESULT WINAPI make_texture(IDirect3DDevice9*,const void*,UINT,UINT width,UINT height,UINT mips,
    DWORD,D3DFORMAT format,D3DPOOL pool,DWORD,DWORD,D3DCOLOR,ImageInfo* info,PALETTEENTRY*,IDirect3DTexture9** out) {
    ++create_calls; received_mips = mips;
    assert(format == D3DFMT_UNKNOWN);
    surface.Width = width; surface.Height = height; surface.Pool = pool;
    if (info) { *info = {}; info->Width = width; info->Height = height; info->Format = surface.Format; }
    *out = SUCCEEDED(create_result) || partial_failure ? reinterpret_cast<IDirect3DTexture9*>(&mock_texture) : nullptr;
    return create_result;
}
static void* __cdecl allocate_mask(size_t size) { ++allocations; return fail_mask ? nullptr : malloc(size); }
static void __cdecl release_mask(void* data) { ++frees; free(data); }
static void reset() {
    caps_result = create_result = desc_result = metadata_result = S_OK;
    create_calls = releases = allocations = frees = 0;
    fail_upload_allocation = partial_failure = fail_mask = has_tag = false;
    returned_mips = 1;
    surface = {}; surface.Format = D3DFMT_A8R8G8B8;
    original_create = make_texture; client_allocate = allocate_mask; client_release = release_mask;
}
static unsigned write_calls, failed_writes;
static bool mock_write(void* destination,const void* data,size_t size) {
    ++write_calls;
    if (failed_writes & (1u << write_calls)) return false;
    memcpy(destination,data,size); return true;
}
static void test_install_guards_and_rollback() {
    uint8_t site[sizeof acv::archive_cache_before], entry[sizeof acv::archive_erase_entry],
        exit[sizeof acv::archive_erase_exit], release[sizeof acv::archive_sized_release];
    memcpy(site,acv::archive_cache_before,sizeof site); memcpy(entry,acv::archive_erase_entry,sizeof entry);
    memcpy(exit,acv::archive_erase_exit,sizeof exit); memcpy(release,acv::archive_sized_release,sizeof release);
    assert(acv::archive_cache_ready(0,site,entry,exit,release));
    assert(!acv::archive_cache_ready(1,site,entry,exit,release));
    struct Guard { uint8_t* bytes; size_t size; } guards[] = {
        {site,sizeof site},{entry,sizeof entry},{exit,sizeof exit},{release,sizeof release}};
    for (auto guard : guards) for (size_t i=0;i<guard.size;++i) {
        guard.bytes[i] ^= 1;
        assert(!acv::archive_cache_ready(0,site,entry,exit,release));
        guard.bytes[i] ^= 1;
    }
    const uint8_t alpha_before[6] = {1,2,3,4,5,6}, jump[6] = {6,5,4,3,2,1};
    const unsigned failures[] = {0,2,4,4|8,8,8|16,8|32,8|16|32};
    for (unsigned failure : failures) {
        uint8_t alpha_site[6]; memcpy(alpha_site,alpha_before,6);
        memcpy(site,acv::archive_cache_before,sizeof site);
        original_create = make_texture; CreateTexture slot = original_create;
        write_calls = 0; failed_writes = failure;
        CreateTexture replacement = hooked_create;
        const MemoryChange changes[] = {{alpha_site,alpha_before,jump,6},
            {&slot,&original_create,&replacement,sizeof replacement},
            {site,acv::archive_cache_before,acv::archive_cache_after,sizeof site}};
        LONG result = install_v64_writes(mock_write,changes,3);
        bool fatal = (failure == (4|8)) || ((failure & 8) && (failure & (16|32)));
        assert(result == (!failure ? 2 : fatal ? -2 : -1));
        if (!failure) {
            assert(slot == hooked_create && !memcmp(alpha_site,jump,6));
            assert(!memcmp(site,acv::archive_cache_after,sizeof site));
        } else {
            assert(!memcmp(site,acv::archive_cache_before,sizeof site));
            if (!fatal) assert(slot == original_create && !memcmp(alpha_site,alpha_before,6));
        }
        if (failure & 8 && failure != (4|8)) assert(write_calls == 5);
    }
}
static BOOL WINAPI mock_archive_read(HANDLE file,LPVOID data,DWORD bytes,LPDWORD count,LPOVERLAPPED overlap) {
    assert(file == HANDLE(17) && data == LPVOID(19) && bytes == 23 && !overlap);
    *count = 29; return TRUE;
}
static LPVOID WINAPI mock_archive_map(HANDLE file,DWORD access,DWORD high,DWORD low,SIZE_T bytes) {
    assert(file == HANDLE(17) && access == 19 && high == 23 && low == 29 && bytes == 31);
    return LPVOID(37);
}
static void test_archive_transaction_and_thunks() {
    // Every write and every rollback position in the complete seven-write candidate.
    for (unsigned stop=1;stop<=7;++stop) for (unsigned rollback=0;rollback<stop;++rollback) {
        uint32_t targets[7], before[7], after[7]; MemoryChange changes[7];
        for (unsigned i=0;i<7;++i) {
            targets[i] = before[i] = i; after[i] = i+100;
            changes[i] = {targets+i,before+i,after+i,4};
        }
        write_calls = 0; failed_writes = 1u << stop;
        if (rollback) failed_writes |= 1u << (stop+rollback);
        LONG result = install_v64_writes(mock_write,changes,7);
        assert(result == (rollback ? -2 : -1) && write_calls == 2*stop-1);
        unsigned changed = 0;
        for (unsigned i=0;i<7;++i) changed += targets[i] != before[i];
        assert(changed == (rollback ? 1u : 0u));
    }
    original_archive_read = mock_archive_read; original_archive_map = mock_archive_map;
    DWORD count = 0;
    assert(hooked_archive_read(HANDLE(17),LPVOID(19),23,&count,nullptr) && count == 29);
    assert(hooked_archive_map(HANDLE(17),19,23,29,31) == LPVOID(37));
    original_archive_read = nullptr; original_archive_map = nullptr;
}
int main() {
    // Refuse before touching any fixed client address, even through the public installer export.
    assert(!AcvInstallV64() && install_state == -1);
    assert(!AcvInstallV64() && !original_create && !original_alpha && !client_allocate && !client_release);
    test_install_guards_and_rollback();
    test_archive_transaction_and_thunks();
    device_table[7] = reinterpret_cast<void*>(&get_caps);
    texture_table[2] = reinterpret_cast<void*>(&release_texture);
    texture_table[4] = reinterpret_cast<void*>(&set_tag);
    texture_table[5] = reinterpret_cast<void*>(&get_tag);
    texture_table[13] = reinterpret_cast<void*>(&level_count);
    texture_table[17] = reinterpret_cast<void*>(&level_desc);
    uint8_t data[160] = {};
    memcpy(data,"DDS ",4); acv::put32(data+4,124); acv::put32(data+76,32);
    acv::put32(data+12,2); acv::put32(data+16,4); acv::put32(data+80,0x41); acv::put32(data+88,32);
    acv::Tag tag = {{'A','C','V','U','I','2','X',0},1,2,1,4,2}; memcpy(data+32,&tag,sizeof tag);
    auto* device = reinterpret_cast<IDirect3DDevice9*>(&mock_device);
    IDirect3DTexture9* texture = nullptr;
    ImageInfo info;
    auto call = [&](UINT mips=0,DWORD usage=0,D3DPOOL pool=D3DPOOL_MANAGED,DWORD mip_filter=3,
                    D3DCOLOR key=0,bool handled=true) {
        memset(&info,0xcd,sizeof info);
        texture = nullptr;
        return create_tagged(device,data,sizeof data,1,1,mips,usage,D3DFMT_UNKNOWN,pool,3,mip_filter,key,
                             &info,nullptr,&texture,handled);
    };
    auto failed = [&]() {
        assert(!texture && info.Width == 2 && info.Height == 1 && info.Depth == 0 && info.Format == D3DFMT_UNKNOWN);
    };
    reset(); caps_result = E_FAIL;
    assert(FAILED(call())); failed(); assert(!create_calls && !releases);
    reset(); create_result = E_OUTOFMEMORY;
    assert(call() == E_OUTOFMEMORY); failed(); assert(!releases);
    reset(); create_result = E_OUTOFMEMORY; partial_failure = true;
    assert(call() == E_OUTOFMEMORY); failed(); assert(releases == 1);
    reset(); desc_result = E_FAIL;
    assert(FAILED(call())); failed(); assert(releases == 1);
    reset(); metadata_result = E_FAIL;
    assert(FAILED(call())); failed(); assert(releases == 1 && !has_tag);
    reset(); returned_mips = 3;
    assert(FAILED(call())); failed(); assert(releases == 1);
    reset(); assert(SUCCEEDED(call()) && texture && received_mips == 1 && !releases);
    assert(info.Width == 2 && info.Height == 1 && has_tag);
    // Cache allocation, reuse and matching native-owner cleanup without invoking client code.
    uint8_t object[0x430] = {}, pixels[32] = {};
    memcpy(object+4,&texture,sizeof texture); acv::put32(object+8,2); acv::put32(object+12,1);
    pixels[3] = 7; pixels[7] = 255; pixels[31] = 200;
    D3DLOCKED_RECT lock{16,pixels};
    auto** cache = reinterpret_cast<uint8_t**>(object+0x42c);
    fail_mask = true;
    assert(!hooked_alpha(object,nullptr,0,0,&lock) && !*cache && allocations == 1 && !frees);
    fail_mask = false; lock.Pitch = 1;
    assert(!hooked_alpha(object,nullptr,0,0,&lock) && !*cache && allocations == 1);
    lock.Pitch = 16;
    assert(hooked_alpha(object,nullptr,0,0,&lock) == 255 && *cache && allocations == 2);
    assert(hooked_alpha(object,nullptr,1,0,&lock) == 200 && allocations == 2);
    client_release(*cache); *cache = nullptr; texture->Release(); assert(frees == 1 && releases == 1);
    // Only a managed, ordinary zero-mip request with one stored level is changed.
    const UINT mip_requests[] = {1u,4u,0xffffffffu};
    for (UINT explicit_mips : mip_requests) {
        reset(); assert(SUCCEEDED(call(explicit_mips)) && received_mips == explicit_mips); texture->Release();
    }
    reset(); acv::put32(data+28,3);
    assert(SUCCEEDED(call()) && !received_mips); texture->Release(); acv::put32(data+28,0);
    reset(); assert(SUCCEEDED(call(0,D3DUSAGE_AUTOGENMIPMAP)) && !received_mips); texture->Release();
    reset(); assert(SUCCEEDED(call(0,0,D3DPOOL_DEFAULT)) && !received_mips); texture->Release();
    for (DWORD skip=1; skip<=31; ++skip) {
        reset(); assert(SUCCEEDED(call(0,0,D3DPOOL_MANAGED,(skip << 26)|3)) && !received_mips);
        texture->Release();
    }
    reset(); assert(SUCCEEDED(call(0,0,D3DPOOL_MANAGED,3,1)) && !received_mips); texture->Release();
    reset(); assert(SUCCEEDED(call(0,0,D3DPOOL_MANAGED,3,0,false)) && !received_mips);
    assert(info.Width == 1 && info.Height == 1 && !has_tag); texture->Release();
    // A valid version-2 palette exercises upload-allocation failure, before D3DX can run.
    uint8_t p8[1160] = {}; memcpy(p8,data,128); acv::put32(p8+40,2); acv::put32(p8+8,0x100f);
    acv::put32(p8+80,0x20); acv::put32(p8+88,8); acv::put32(p8+108,0x1000);
    reset(); fail_upload_allocation = true; memset(&info,0xcd,sizeof info);
    assert(create_tagged(device,p8,sizeof p8,1,1,0,0,D3DFMT_UNKNOWN,D3DPOOL_MANAGED,3,3,0,
                         &info,nullptr,&texture,true) == E_OUTOFMEMORY);
    failed(); assert(!create_calls && !releases);
    puts("PASS: native cache guards/install rollback, upload ownership/failure outputs, mip policy and alpha-cache lifecycle");
}
