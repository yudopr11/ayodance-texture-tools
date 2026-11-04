// CPU-only candidate policies and temporary-file I/O. No game access, hooks or GPU.
#include "archive_io.hpp"
#include "archive_file_read.hpp"
#include "archive_io_guards.hpp"
#include <windows.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>

struct ReadFixture {
    struct Block { void* data; size_t size; } blocks[4]{};
    unsigned allocations = 0, releases = 0, reads = 0;
    bool fail_allocate = false, fail_read = false, short_read = false;
    uint32_t stored = 0;
    static void* allocate(void* context,size_t size) {
        auto& f = *static_cast<ReadFixture*>(context);
        if (f.fail_allocate) return nullptr;
        void* p = malloc(size ? size : 1); assert(p);
        assert(f.allocations < 4); f.blocks[f.allocations++] = {p,size};
        return p;
    }
    static void release(void* context,void* data) {
        auto& f = *static_cast<ReadFixture*>(context);
        for (auto& b : f.blocks) if (b.data == data) {
            b.data = nullptr; ++f.releases; free(data); return;
        }
        assert(false && "Double/foreign free");
    }
    static bool read(void* context,uint32_t offset,void* data,uint32_t size,uint32_t* count) {
        auto& f = *static_cast<ReadFixture*>(context); ++f.reads;
        assert(offset == 4096 && size == f.stored);
        size_t capacity = 0;
        for (auto& b : f.blocks) if (b.data == data) capacity = b.size;
        assert(size <= capacity);
        *count = size - unsigned(f.short_read && size);
        memset(data,0x79,*count);
        return !f.fail_read;
    }
    acv::ArchiveReadOps ops() { return {this,allocate,release,read}; }
};
static void reads() {
    const uint32_t owners[] = {1,37,255,256,262,267,330,499};
    const uint32_t sizes[] = {16,32,48};
    for (uint32_t owner : owners) for (uint32_t stored : sizes) {
        ReadFixture f; f.stored = stored;
        void* owned = ReadFixture::allocate(&f,32);
        auto entry = acv::ArchiveReadEntry{32,stored,4096,owner};
        uint32_t received = 999;
        assert(acv::read_archive_fallback(owned,owned,32,entry,owner,4096+stored,f.ops(),received));
        assert(owned && received == stored && f.reads == 1);
        assert(f.allocations == (stored > 32 ? 2u : 1u));
        ReadFixture::release(&f,owned); // Native resource destructor owns the published buffer.
        assert(f.allocations == f.releases);
    }
    for (unsigned failure=0;failure<10;++failure) {
        ReadFixture f; f.stored = 48;
        void* owned = ReadFixture::allocate(&f,32);
        void* requested = owned;
        auto entry = acv::ArchiveReadEntry{32,48,4096,267};
        uint64_t file = 4096+48;
        uint32_t owner=267, raw=32, received=999;
        switch (failure) {
        case 0: f.fail_allocate = true; break;
        case 1: f.fail_read = true; break;
        case 2: f.short_read = true; break;
        case 3: entry.stored = acv::archive_payload_limit+1; break;
        case 4: entry.raw = acv::archive_payload_limit+1; raw = entry.raw; break;
        case 5: --file; break;
        case 6: entry.offset = 0xffffffffu; file = 0x100000000ull; break;
        case 7: owner = 6; break;
        case 8: raw = 31; break;
        case 9: requested = &f; break;
        }
        assert(!acv::read_archive_fallback(owned,requested,raw,entry,owner,file,f.ops(),received));
        assert(!owned && !received && f.allocations == f.releases);
    }
    assert(acv::archive_scratch_bytes(7,16) == 16);
    assert(acv::archive_scratch_bytes(100,50) == 120);
    assert(acv::archive_scratch_bytes(acv::archive_payload_limit,1) >= acv::archive_payload_limit);
    assert(!acv::archive_scratch_bytes(acv::archive_payload_limit+1,1));
}
struct ViewFixture {
    uintptr_t next = 0x10000;
    unsigned maps=0, unmaps=0;
    bool fail_map=false, fail_unmap=false;
    static void* map(void* context,uint64_t bytes) {
        auto& f = *static_cast<ViewFixture*>(context); ++f.maps;
        assert(bytes);
        if (f.fail_map) return nullptr;
        f.next += uintptr_t(bytes); return reinterpret_cast<void*>(f.next);
    }
    static bool unmap(void* context,void*) {
        auto& f = *static_cast<ViewFixture*>(context); ++f.unmaps; return !f.fail_unmap;
    }
};
static void views() {
    ViewFixture f;
    acv::ArchiveViews v(3*4096);
    void* retained = v.map(4097,&f,ViewFixture::map);
    void* temporary = v.map(1,&f,ViewFixture::map);
    assert(retained && temporary && v.charged == 3*4096);
    assert(!v.map(1,&f,ViewFixture::map) && f.maps == 2); // No eviction under pressure.
    assert(!v.unmap(reinterpret_cast<void*>(1),&f,ViewFixture::unmap) && !f.unmaps);
    f.fail_unmap = true;
    assert(!v.unmap(retained,&f,ViewFixture::unmap) && v.charged == 3*4096);
    f.fail_unmap = false;
    assert(v.unmap(temporary,&f,ViewFixture::unmap) && v.charged == 2*4096);
    assert(!v.unmap(temporary,&f,ViewFixture::unmap) && v.charged == 2*4096);
    f.fail_map = true;
    assert(!v.map(4096,&f,ViewFixture::map) && v.charged == 2*4096);
    assert(!v.map(UINT64_MAX,&f,ViewFixture::map) && v.charged == 2*4096);
    assert(v.unmap(retained,&f,ViewFixture::unmap) && !v.charged);
    acv::ArchiveViews full(503*4096);
    f.fail_map = false;
    for (unsigned i=0;i<502;++i) assert(full.map(1,&f,ViewFixture::map));
    assert(!full.map(1,&f,ViewFixture::map) && full.charged == 502*4096);
}
static void prefixes_and_guards() {
    acv::ArchiveIndexRandom mt(5489);
    const uint32_t known[] = {3499211612u,581869302u,3890346734u,3586334585u,545404204u};
    for (uint32_t value : known) assert(mt.next() == value);
    const uintptr_t base = 0x5a6000;
    uint8_t* image = static_cast<uint8_t*>(calloc(1,0x6000)); assert(image);
    for (const auto& guard : acv::archive_io_guards) memcpy(image+guard.address-base,guard.bytes,guard.size);
    assert(acv::archive_io_ready(image,base));
    for (const auto& guard : acv::archive_io_guards) for (size_t i=0;i<guard.size;++i) {
        image[guard.address-base+i] ^= 1;
        assert(!acv::archive_io_ready(image,base));
        image[guard.address-base+i] ^= 1;
    }
    free(image);
}
static bool read_file(void* context,uint32_t offset,void* data,uint32_t size,uint32_t* received) {
    DWORD error = ERROR_SUCCESS;
    return acv::archive_read_at(ReadFile,HANDLE(context),offset,data,size,received,error);
}
struct ConcurrentRead {
    HANDLE file;
    unsigned seed;
    static DWORD WINAPI run(void* argument) {
        auto& input = *static_cast<ConcurrentRead*>(argument);
        for (unsigned i=0;i<64;++i) {
            uint32_t offset = (input.seed*257+i*97) % (4096-96);
            uint8_t data[96]; uint32_t received = 0; DWORD error = ERROR_SUCCESS;
            assert(acv::archive_read_at(ReadFile,input.file,offset,data,sizeof data,&received,error));
            assert(received == sizeof data && error == ERROR_SUCCESS);
            for (unsigned j=0;j<sizeof data;++j) assert(data[j] == (offset+j)%251);
        }
        return 0;
    }
};
static void concurrent_file_reads() {
    char directory[MAX_PATH],name[MAX_PATH];
    assert(GetTempPathA(MAX_PATH,directory) && GetTempFileNameA(directory,"acv",0,name));
    HANDLE file = CreateFileA(name,GENERIC_READ|GENERIC_WRITE,
        FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,nullptr,OPEN_EXISTING,
        FILE_ATTRIBUTE_TEMPORARY|FILE_FLAG_DELETE_ON_CLOSE,nullptr);
    assert(file != INVALID_HANDLE_VALUE);
    uint8_t bytes[4096];
    for (unsigned i=0;i<sizeof bytes;++i) bytes[i] = uint8_t(i%251);
    DWORD written = 0;
    assert(WriteFile(file,bytes,sizeof bytes,&written,nullptr) && written == sizeof bytes);
    LARGE_INTEGER position{}; position.QuadPart = 7;
    assert(SetFilePointerEx(file,position,nullptr,FILE_BEGIN));
    ConcurrentRead inputs[4]{}; HANDLE threads[4]{};
    for (unsigned i=0;i<4;++i) {
        inputs[i] = {file,i};
        threads[i] = CreateThread(nullptr,0,ConcurrentRead::run,&inputs[i],0,nullptr);
        assert(threads[i]);
    }
    assert(WaitForMultipleObjects(4,threads,TRUE,30000) == WAIT_OBJECT_0);
    for (HANDLE thread : threads) assert(CloseHandle(thread));
    LARGE_INTEGER zero{},current{};
    assert(SetFilePointerEx(file,zero,&current,FILE_CURRENT) && current.QuadPart == 7);
    uint32_t received = 999; DWORD error = ERROR_SUCCESS;
    assert(!acv::archive_read_at(ReadFile,file,4090,bytes,16,&received,error));
    assert(received == 6 && error == ERROR_HANDLE_EOF);
    assert(CloseHandle(file)); // Deletes only this test's newly created temporary file.
}
int main(int argc,char** argv) {
    if (argc == 4 && !strcmp(argv[1],"--prefix")) {
        char* end = nullptr; unsigned long owner = strtoul(argv[2],&end,10);
        if (!*argv[2] || !end || *end || owner >= 500) return 2;
        HANDLE file = CreateFileA(argv[3],GENERIC_READ,FILE_SHARE_READ,nullptr,OPEN_EXISTING,0,nullptr);
        if (file == INVALID_HANDLE_VALUE) return 2;
        LARGE_INTEGER size{}; uint32_t prefix = 0;
        bool ok = GetFileSizeEx(file,&size) && size.QuadPart >= 0 &&
            acv::archive_index_extent({file,read_file},uint64_t(size.QuadPart),uint32_t(owner),prefix);
        CloseHandle(file);
        if (!ok) { puts("{\"status\":\"REJECTED\"}"); return 1; }
        printf("{\"status\":\"PASS\",\"prefix\":%u}\n",prefix); return 0;
    }
    if (argc != 1) return 2;
    reads(); views(); prefixes_and_guards(); concurrent_file_reads();
    puts("archive_io_test: PASS (exact reads, concurrent private I/O, unchanged native cursor, ownership, failure cleanup, scratch, non-evicting page budget)");
}
