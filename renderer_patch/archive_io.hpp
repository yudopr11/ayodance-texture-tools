#pragma once
#include <stdint.h>
#include <stddef.h>

namespace acv {
constexpr uint32_t archive_payload_limit = 64u << 20;
struct ArchiveReadEntry {
    uint32_t raw, stored, offset, owner;
};
struct ArchiveReadOps {
    void* context;
    void* (*allocate)(void*,size_t);
    void (*release)(void*,void*);
    bool (*read_at)(void*,uint32_t,void*,uint32_t,uint32_t*);
};

// The native caller ignores ReadFile's result: null its owned field on every failure.
inline bool read_archive_fallback(void*& owned,void* requested_buffer,uint32_t requested,
    const ArchiveReadEntry& entry,uint32_t owner,uint64_t file_size,
    const ArchiveReadOps& ops,uint32_t& received) {
    received = 0;
    bool valid = owned && owned == requested_buffer && requested == entry.raw &&
        owner < 500 && owner == entry.owner && entry.raw <= archive_payload_limit &&
        entry.stored <= archive_payload_limit && uint64_t(entry.offset) <= file_size &&
        uint64_t(entry.stored) <= file_size - uint64_t(entry.offset);
    if (valid && entry.stored > requested) {
        void* replacement = ops.allocate(ops.context,entry.stored);
        if (replacement) {
            void* before = owned;
            owned = replacement;
            ops.release(ops.context,before);
        } else valid = false;
    }
    uint32_t count = 0;
    if (valid && ops.read_at(ops.context,entry.offset,owned,entry.stored,&count) && count == entry.stored) {
        received = count;
        return true;
    }
    void* before = owned;
    owned = nullptr;
    if (before) ops.release(ops.context,before);
    return false;
}

// Native crypto decrypts into the scratch allocation before inflating into it.
inline size_t archive_scratch_bytes(uint32_t raw,uint32_t stored) {
    if (raw > archive_payload_limit || stored > archive_payload_limit) return 0;
    uint32_t native = uint32_t(float(raw) * 1.2f);
    return native > stored ? native : stored;
}

// shortcut: first five MT19937 words only; implement a full twist if an envelope needs more.
struct ArchiveIndexRandom {
    uint32_t words[624]; unsigned index = 0;
    explicit ArchiveIndexRandom(uint32_t seed) {
        words[0] = seed;
        for (uint32_t i=1;i<624;++i) words[i] =
            (words[i-1] ^ (words[i-1] >> 30)) * 1812433253u + i;
    }
    uint32_t next() {
        uint32_t value = (words[index] & 0x80000000u) | (words[index+1] & 0x7fffffffu);
        value = words[index+397] ^ (value >> 1) ^ ((value & 1) ? 0x9908b0dfu : 0);
        ++index;
        value ^= value >> 11;
        value ^= (value << 7) & 0x9d2c5680u;
        value ^= (value << 15) & 0xefc60000u;
        return value ^ (value >> 18);
    }
};
struct ArchivePrefixReader {
    void* context;
    bool (*read_at)(void*,uint32_t,void*,uint32_t,uint32_t*);
    bool word(uint32_t offset,uint32_t size,uint32_t& value) const {
        if (size != 2 && size != 4) return false;
        uint8_t data[4]{}; uint32_t count = 0;
        if (!read_at(context,offset,data,size,&count) || count != size) return false;
        value = uint32_t(data[0]) | (uint32_t(data[1])<<8) |
            (uint32_t(data[2])<<16) | (uint32_t(data[3])<<24);
        return true;
    }
};
// Determine only the wire index extent; crypto and decoded-record validation remain native.
inline bool archive_index_extent(const ArchivePrefixReader& reader,uint64_t file_size,
    uint32_t owner,uint32_t& prefix) {
    prefix = 0;
    uint32_t tag = 0, count = 0;
    if (owner >= 500 || file_size < 4 || !reader.word(0,4,tag)) return false;
    uint32_t variant = (tag >> 24) == 255 ? ((tag >> 16) & 255) : 0;
    bool tagged = (tag >> 24) == 255;
    if (tagged) {
        if ((tag & 65535) != owner || file_size < 8 || !reader.word(4,4,count)) return false;
    } else count = tag;
    uint32_t width = !tagged ? 176 : variant == 6 ? 212 : 180;
    if (count > archive_payload_limit / width) return false;
    uint32_t base = tagged ? 8 : 4, length = 0;
    if (!tagged || variant == 1) length = count * width;
    else if (variant == 2 || variant == 3) {
        if (file_size < 10 || !reader.word(8,2,length)) return false;
        base = 10;
        if (!length) return false;
    } else if (variant == 5 || variant == 6) {
        uint32_t encoded = 0, mask = 0;
        if (file_size < 16 || !reader.word(8,4,encoded) || !reader.word(12,4,mask)) return false;
        uint32_t marked = encoded ^ mask;
        if ((marked & 0x7fffffffu) != count) return false;
        ArchiveIndexRandom random(count);
        if (marked >> 31) for (unsigned i=0;i<4;++i) (void)random.next();
        base = 16;
        length = mask;
        if (count > 4) {
            uint32_t replacement = 8 + random.next() % (count * 8 - 15);
            uint32_t field = 0;
            if (uint64_t(base) + replacement + 4 > file_size ||
                !reader.word(base+replacement,4,field)) return false;
            length = field ^ mask;
            if (replacement > length || length - replacement < 4) return false;
        }
        if (!length) return false;
    } else return false;
    if (length > archive_payload_limit || uint64_t(base) + length > file_size) return false;
    prefix = base + length;
    return true;
}

// Caller serializes access; OS callbacks must not reenter. Never evict borrowed views.
struct ArchiveViews {
    struct View { void* base; uint64_t bytes; } views[502]{};
    uint64_t limit, charged = 0;
    constexpr explicit ArchiveViews(uint64_t bytes) : limit(bytes) {}
    bool owns(void* base) const {
        for (const auto& view : views) if (view.base && view.base == base) return true;
        return false;
    }
    void* map(uint64_t bytes,void* context,void* (*create)(void*,uint64_t)) {
        if (!bytes || bytes > UINT64_MAX - 4095) return nullptr;
        uint64_t requested = bytes;
        bytes = (bytes + 4095) & ~uint64_t(4095);
        if (charged > limit || bytes > limit - charged) return nullptr;
        View* vacant = nullptr;
        for (auto& view : views) if (!view.base) { vacant = &view; break; }
        if (!vacant) return nullptr;
        charged += bytes;
        void* base = create(context,requested);
        if (!base) { charged -= bytes; return nullptr; }
        *vacant = {base,bytes};
        return base;
    }
    bool unmap(void* base,void* context,bool (*destroy)(void*,void*)) {
        for (auto& view : views) if (view.base && view.base == base) {
            if (!destroy(context,base)) return false;
            charged -= view.bytes;
            view = {};
            return true;
        }
        return false;
    }
};
} // namespace acv
