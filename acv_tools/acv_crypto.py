"""ACV's deterministic MT/permutation and archive-specific Blowfish tables.

Derived from recovered native functions 005aa1a0, 00cb5620, 00cb7700,
00c3a1c0, 00c39260. No game image or runtime is required.
"""

import struct
from functools import lru_cache

from Crypto.Cipher import Blowfish
from py3rijndael import Rijndael

MASK = 0xffffffff


class MT19937:
    def __init__(self, seed):
        self.words = [seed & MASK]
        for index in range(1, 624):
            previous = self.words[-1]
            self.words.append(((previous ^ (previous >> 30)) * 1812433253 + index) & MASK)
        self.index = 624

    def next(self):
        if self.index == 624:
            for index in range(624):
                value = (self.words[index] & 0x80000000) | (self.words[(index + 1) % 624] & 0x7fffffff)
                self.words[index] = self.words[(index + 397) % 624] ^ (value >> 1) ^ (0x9908b0df if value & 1 else 0)
            self.index = 0
        value = self.words[self.index]
        self.index += 1
        value ^= value >> 11
        value ^= (value << 7) & 0x9d2c5680
        value ^= (value << 15) & 0xefc60000
        return (value ^ (value >> 18)) & MASK

    def between(self, low, high):
        if high < low:
            raise ValueError("Invalid MT interval")
        return low + self.next() % (high - low + 1)


# Native permutation table at 02419ca0 is sparse after 1009, not consecutive primes.
PRIMES = (
    2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37,
    41, 43, 47, 53, 59, 61, 67, 71, 73, 79, 83, 89,
    97, 101, 103, 107, 109, 113, 127, 131, 137, 139, 149, 151,
    157, 163, 167, 173, 179, 181, 191, 193, 197, 199, 211, 223,
    227, 229, 233, 239, 241, 251, 257, 263, 269, 271, 277, 281,
    283, 293, 307, 311, 313, 317, 331, 337, 347, 349, 353, 359,
    367, 373, 379, 383, 389, 397, 401, 409, 419, 421, 431, 433,
    439, 443, 449, 457, 461, 463, 467, 479, 487, 491, 499, 503,
    509, 521, 523, 541, 547, 557, 563, 569, 571, 577, 587, 593,
    599, 601, 607, 613, 617, 619, 631, 641, 643, 647, 653, 659,
    661, 673, 677, 683, 691, 701, 709, 719, 727, 733, 739, 743,
    751, 757, 761, 769, 773, 787, 797, 809, 811, 821, 823, 827,
    829, 839, 853, 857, 859, 863, 877, 881, 883, 887, 907, 911,
    919, 929, 937, 941, 947, 953, 967, 971, 977, 983, 991, 997,
    1009, 1051, 1103, 1151, 1201, 1259, 1301, 1361, 1409, 1451, 1511, 1553,
    1601, 1657, 1709, 1753, 1801, 1861, 1901, 1951, 2003, 2053, 2111, 2113,
    2153, 2203, 2251, 2309, 2351, 2411, 2459, 2503, 2551, 2557, 2609, 2657,
    2707, 2753, 2767, 2801, 2851, 2903, 2953, 3001, 3061, 3109, 3163, 3203,
    3251, 3301, 3359, 3407, 3457, 3511, 3557, 3607, 3659, 3701, 3761, 3803,
    3851, 3907, 3967, 4001, 4051, 4111, 4153, 4201, 4253, 4327, 4357, 4409,
    4451, 4507, 4561, 4603, 4651, 4703, 4751, 4801, 4861, 4903, 4951, 5003,
    5101, 5209, 5303, 5407, 5501, 5623, 5701, 5801, 5903, 6007, 6101, 6211,
    6301, 6421, 6521, 6607, 6701, 6803, 6907, 7001, 7103, 7207, 7307, 7411,
    7507, 7603, 7703, 7817, 7901, 8009, 8101, 8209, 8311, 8419, 8501, 8609,
    8707, 8803, 8923, 9001, 9103, 9203, 9311, 9403, 9511, 9601, 9719, 9803,
    9901, 10007, 10501, 11003, 11503, 12007, 12503, 13001, 13513, 14009, 14503, 15013,
    15511, 16033, 16519, 17011, 17509, 18013, 18503, 19001, 19501, 20011, 20507, 21001,
    21503, 22003, 22501, 23003, 23509, 24001, 24509, 25013, 26003, 27011, 28001, 29009,
    30011, 31013, 31991, 32999, 33997, 34981, 35999, 36997, 37997, 38993, 39989, 40993,
    41999, 42989, 43997, 44987, 45989, 46997, 47981, 48991, 49999, 59999, 69997, 79999,
    89989, 99991, 109987, 119993, 129971, 139999, 149993, 159979, 169991, 179999, 189997, 199999,
    209987, 219983, 229981, 239999, 249989, 259993, 269987, 279991, 289999, 299993, 309989, 319993,
    329999, 339991, 349981,
)


def permutation(size, first, second, third):
    if not 1 <= size <= PRIMES[-1] or not all(0 <= index < len(PRIMES) for index in (first, second, third)):
        raise ValueError("Permutation parameters outside recovered range")
    random = MT19937(size)
    a, b, c = (random.between(0, PRIMES[index]) for index in (first, second, third))
    step = (c + 1 + ((a + 1) * size + 1 + b) * size) & 0x3fffffff
    modulus = next(prime for prime in PRIMES if prime >= size)
    if step % modulus == 0:
        step += 1
    result = []
    position = 0
    for _ in range(modulus):
        position = (step + position) % modulus
        if position < size:
            result.append(position)
    return result


def archive_tables(tag):
    random = MT19937(tag ^ 0x49444e)
    tables = []
    for size, ranges, limit, offset in (
        (18, ((13, 17), (6, 12), (1, 5)), 0xe8ddf945, 0x03707344),
        (1024, ((200, 300), (60, 150), (5, 30)), 0xff0ef701, 0x00250e2d),
    ):
        upper = [random.between(low, high) for low, high in ranges]
        indices = [random.between(0, bound) for bound in upper]
        values = [0] * size
        for index in permutation(size, *indices):
            values[index] = (random.next() % limit + offset) & MASK
        tables.append(values)
    return tables


class ArchiveBlowfish:
    def __init__(self, key, tag):
        if not 1 <= len(key) <= 56:
            raise ValueError("Blowfish key must contain 1..56 bytes")
        self.p, self.s = archive_tables(tag)
        expanded = (key * ((72 + len(key) - 1) // len(key)))[:72]
        self.p = [word ^ part for word, part in zip(self.p, struct.unpack(">18I", expanded), strict=True)]
        left = right = 0
        for table in (self.p, self.s):
            for index in range(0, len(table), 2):
                left, right = self.block(left, right, self.p)
                table[index:index + 2] = left, right

    def f(self, word):
        a, b, c, d = word >> 24, (word >> 16) & 255, (word >> 8) & 255, word & 255
        return (((self.s[a] + self.s[256 + b]) & MASK) ^ self.s[512 + c]) + self.s[768 + d] & MASK

    def block(self, left, right, rounds):
        for index in range(16):
            left ^= rounds[index]
            right ^= self.f(left)
            left, right = right, left
        left, right = right, left
        right ^= rounds[16]
        left ^= rounds[17]
        return left, right

    def crypt(self, data, *, decrypt):
        if len(data) % 8:
            raise ValueError("Blowfish input is not block aligned")
        rounds = self.p[::-1] if decrypt else self.p
        result = bytearray(len(data))
        for offset in range(0, len(data), 8):
            left, right = self.block(*struct.unpack_from(">2I", data, offset), rounds)
            struct.pack_into(">2I", result, offset, left, right)
        return bytes(result)


BLOWFISH_PAYLOAD_KEY = bytes.fromhex("b5a5c0ccc5b8c6c4c0cf20bfa3c5a9b8b3c6ae2fb5f0c5a9b8b3c6ae20c5b0")
LEGACY_PAYLOAD_KEY = bytes.fromhex("c6bcbeb2b8aebfa3c5cdc5d7c0ceb8d5c6aeb5a5c0ccc5b8c6c4c0cf")
HEADER_KEYS = {
    1: bytes.fromhex("bfc0b5f0bcc7b5a5c0ccc5b8c6c4c0cfc7ecb4f5c5b0"),
    2: bytes.fromhex("bfc0b5f0bcc7b5a5c0ccc5b8c6c4c0cfc7ecb4f5c5b0"),
    3: bytes.fromhex("414356b5a5c0ccc5b8c6c4c0cf20c7ecb4f520c5b0"),
    5: bytes.fromhex("414356b5a5c0ccc5b8c6c4c0cf20c7ecb4f520c5b028bcc5c7c329"),
    6: b"asfjbnbcxz,me.;2321.fas;vzvbuyrt",
}
# Payload factory passes its override flag as zero and ignores the nominal key.
RIJNDAEL_PAYLOAD_KEY = b"uiouk.mvn,mcasdf,dsagfdsvghkyewc".ljust(32, b"\0")
RIJNDAEL_IV = b"gawwegsbffjrgdsdsanthkuryjkihgbv"


@lru_cache(maxsize=512)
def blowfish_cipher(key, tag):
    return ArchiveBlowfish(key, tag)


@lru_cache(maxsize=4)
def rijndael_cipher(key):
    return Rijndael(key, block_size=32)


def crypt(data, tag, *, header=False, decrypt=True, iv=None):
    variant = (tag >> 16) & 255
    if tag >> 24 != 255 or variant not in HEADER_KEYS or (tag & 65535) >= 500:
        raise ValueError(f"Unverified ACV crypto tag {tag:08x}")
    if variant == 6 or iv is not None:
        key = HEADER_KEYS[6] if header else RIJNDAEL_PAYLOAD_KEY
        previous = RIJNDAEL_IV if iv is None else iv
        if len(previous) != 32 or len(data) % 32:
            raise ValueError("Invalid Rijndael IV or block alignment")
        cipher = rijndael_cipher(key)
        output = bytearray()
        for offset in range(0, len(data), 32):
            block = data[offset:offset + 32]
            if decrypt:
                output.extend(a ^ b for a, b in zip(cipher.decrypt(block), previous, strict=True))
                previous = block
            else:
                previous = cipher.encrypt(bytes(a ^ b for a, b in zip(block, previous, strict=True)))
                output.extend(previous)
        return bytes(output)
    key = HEADER_KEYS[variant] if header else (BLOWFISH_PAYLOAD_KEY if variant in (3, 5) else LEGACY_PAYLOAD_KEY)
    key = key.ljust(56, b"\0")
    if len(data) % 8:
        raise ValueError("Invalid Blowfish block alignment")
    if variant in (1, 2):
        # Required by the legacy file format, not for protecting new secrets.
        cipher = Blowfish.new(key, Blowfish.MODE_ECB)  # noqa: S304  # nosec B304
        return cipher.decrypt(data) if decrypt else cipher.encrypt(data)
    return blowfish_cipher(key, tag).crypt(data, decrypt=decrypt)


def self_test():
    random = MT19937(5489)
    assert [random.next() for _ in range(5)] == [3499211612, 581869302, 3890346734, 3586334585, 545404204]
    for size in (10, 18, 20, 1024):
        assert sorted(permutation(size, 4, 3, 2)) == list(range(size))
    cipher = ArchiveBlowfish(bytes(range(56)), 0xff050002)
    data = bytes(range(64))
    assert cipher.crypt(cipher.crypt(data, decrypt=False), decrypt=True) == data
    print("ACV crypto self-check passed: canonical MT outputs, complete permutations, custom cipher roundtrip.")


if __name__ == "__main__":
    self_test()
