#!/usr/bin/env python3
"""Generate a throwaway Safex mainnet address (TESTING ONLY - keys are random, do NOT use for funds)."""
import os, sys
RC = [0x0000000000000001,0x0000000000008082,0x800000000000808A,0x8000000080008000,0x000000000000808B,0x0000000080000001,
      0x8000000080008081,0x8000000000008009,0x000000000000008A,0x0000000000000088,0x0000000080008009,0x000000008000000A,
      0x000000008000808B,0x800000000000008B,0x8000000000008089,0x8000000000008003,0x8000000000008002,0x8000000000000080,
      0x000000000000800A,0x800000008000000A,0x8000000080008081,0x8000000000008080,0x0000000080000001,0x8000000080008008]
ROT = [[0,36,3,41,18],[1,44,10,45,2],[62,6,43,15,61],[28,55,25,21,56],[27,20,39,8,14]]
M = (1 << 64) - 1
def rol(x, n): return ((x << n) | (x >> (64 - n))) & M if n else x
def keccak_f(A):
    for rnd in range(24):
        C = [A[x][0] ^ A[x][1] ^ A[x][2] ^ A[x][3] ^ A[x][4] for x in range(5)]
        D = [C[(x - 1) % 5] ^ rol(C[(x + 1) % 5], 1) for x in range(5)]
        A = [[A[x][y] ^ D[x] for y in range(5)] for x in range(5)]
        B = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                B[y][(2 * x + 3 * y) % 5] = rol(A[x][y], ROT[x][y])
        A = [[B[x][y] ^ ((~B[(x + 1) % 5][y]) & B[(x + 2) % 5][y]) for y in range(5)] for x in range(5)]
        A[0][0] ^= RC[rnd]
    return A
def keccak256(data):
    rate = 136
    data = bytearray(data) + b'\x01'
    while len(data) % rate: data += b'\x00'
    data[-1] |= 0x80
    A = [[0] * 5 for _ in range(5)]
    for off in range(0, len(data), rate):
        blk = data[off:off + rate]
        for i in range(rate // 8):
            A[i % 5][i // 5] ^= int.from_bytes(blk[8 * i:8 * i + 8], 'little')
        A = keccak_f(A)
    return b''.join(A[i % 5][i // 5].to_bytes(8, 'little') for i in range(4))
q = 2**255 - 19
l = 2**252 + 27742317777372353535851937790883648493
d = -121665 * pow(121666, q - 2, q) % q
I = pow(2, (q - 1) // 4, q)
def xrecover(y):
    xx = (y * y - 1) * pow(d * y * y + 1, q - 2, q)
    x = pow(xx, (q + 3) // 8, q)
    if (x * x - xx) % q: x = x * I % q
    if x % 2: x = q - x
    return x
By = 4 * pow(5, q - 2, q) % q
Bp = (xrecover(By), By)
def add(P, Q):
    x1, y1 = P; x2, y2 = Q
    x3 = (x1 * y2 + x2 * y1) * pow(1 + d * x1 * x2 * y1 * y2, q - 2, q)
    y3 = (y1 * y2 + x1 * x2) * pow(1 - d * x1 * x2 * y1 * y2, q - 2, q)
    return (x3 % q, y3 % q)
def mul(P, e):
    R = (0, 1)
    while e:
        if e & 1: R = add(R, P)
        P = add(P, P); e >>= 1
    return R
def enc(P):
    x, y = P
    return (y | ((x & 1) << 255)).to_bytes(32, 'little')
ALPH = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
ENC_SIZES = [0, 2, 3, 5, 6, 7, 9, 10, 11]
def b58_block(b):
    n = int.from_bytes(b, 'big'); s = ''
    for _ in range(ENC_SIZES[len(b)]):
        n, r = divmod(n, 58); s = ALPH[r] + s
    return s
def b58(data): return ''.join(b58_block(data[i:i + 8]) for i in range(0, len(data), 8))
def varint(n):
    out = bytearray()
    while n >= 0x80:
        out.append((n & 0x7f) | 0x80); n >>= 7
    out.append(n); return bytes(out)
def gen(prefix=0x10003798):
    ssk = int.from_bytes(os.urandom(64), 'little') % l
    vsk = int.from_bytes(keccak256(ssk.to_bytes(32, 'little')), 'little') % l
    data = varint(prefix) + enc(mul(Bp, ssk)) + enc(mul(Bp, vsk))
    data += keccak256(data)[:4]
    return b58(data)
if __name__ == '__main__':
    assert keccak256(b'').hex() == 'c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470'
    print(gen())
