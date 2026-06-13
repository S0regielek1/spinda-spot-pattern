#!/usr/bin/env python3
"""
パッチール模様数の解析的厳密カウント（包除原理の機械化・完全版）

総当たり（全 2^32 描画）とは独立な第3の経路で、第三世代パッチールの
見た目ユニーク数を厳密に求める。結果は 3,880,873,934 で総当たりと一致する。

方法:
 1. pokeruby の ROM 由来生バイナリ (spot_*.bin) と pokeemerald の
    anim_front.png から、DRAW_SPINDA_SPOTS の意味論を忠実に再現して
    各スポット 256 位置の「可視ピクセル集合」を計算（u8 巻き戻り込み）。
    → per-spot 異なり数 {253, 256, 236, 256} を検証。
 2. スポット 0,2,3 の全クラス組 (253×236×256 = 15,285,248) を列挙し、
    画像をスポット1の可動領域 P1 で内外に分割。
    外側画像でグループ化し、各グループ内で「係争領域の値 × スポット1の
    256 クラス」の合成の異なり数を厳密に数えて合算する。
    （ハッシュは外側画像のグループ化キーのみ。128bit、鍵を変えた
      2回実行で衝突がないことを照合する。）

実行: python3 analytic_count.py        (要: pillow, ネットワーク)
所要: 数分（列挙 ~30s + ソート + 集計 ~50s × 2シード)
"""
import hashlib
import os
import pickle
import struct
import subprocess
import sys
import time
import urllib.request

RAW_E = "https://raw.githubusercontent.com/pret/pokeemerald/master/"
RAW_R = "https://raw.githubusercontent.com/pret/pokeruby/master/"
ANCHORS = [(16, 7), (40, 8), (22, 25), (34, 26)]  # gSpindaSpotGraphics
EXPECT_PER_SPOT = [253, 256, 236, 256]
BRUTE_FORCE_RESULT = 3_880_873_934


def fetch(url, path):
    if not os.path.exists(path):
        urllib.request.urlretrieve(url, path)


def load_assets():
    from PIL import Image
    for i in range(4):
        # ROM実バイト: u16 LE ×16行、ゲームは bit0=左端 から描く
        fetch(RAW_R + f"graphics/spinda_spots/spot_{i}.bin", f"spot_{i}.bin")
    fetch(RAW_E + "graphics/pokemon/spinda/anim_front.png", "anim_front.png")
    spots = [struct.unpack("<16H", open(f"spot_{i}.bin", "rb").read())
             for i in range(4)]
    im = Image.open("anim_front.png")
    assert im.mode == "P"
    px = im.load()
    # 肌色 = パレット 1..3 (FIRST_SPOT_COLOR..LAST_SPOT_COLOR)
    skin = [[1 <= px[x, y] <= 3 for x in range(64)] for y in range(64)]
    return spots, skin


def visible_mask(spots, skin, i, nx, ny):
    """DRAW_SPINDA_SPOTS 忠実再現: スポット i、ニブル (nx,ny) の可視ピクセル集合"""
    ax, ay = ANCHORS[i]
    x0 = (ax + nx - 8) & 0xFF          # u8（x は実際には巻き戻らない）
    y = (ay + ny - 8) & 0xFF           # u8（スポット0で y=255 に巻き戻る）
    bits = 0
    for row in range(16):
        r = spots[i][row]
        if r and y < 64:               # y=255 等の行は可視フレーム外
            for j in range(16):        # bit0 が左端（gbagfx PNG とは左右逆）
                if (r >> j) & 1:
                    c = x0 + j
                    if c < 64 and skin[y][c]:
                        bits |= 1 << (y * 64 + c)
        y = (y + 1) & 0xFF
    return bits


def bitlist(x):
    out, i = [], 0
    while x:
        if x & 1:
            out.append(i)
        x >>= 1
        i += 1
    return out


def main():
    spots, skin = load_assets()

    classes = []
    for i in range(4):
        seen = {}
        for n in range(256):
            m = visible_mask(spots, skin, i, n & 0xF, n >> 4)
            seen.setdefault(m, []).append(n)
        classes.append(list(seen.keys()))
    per_spot = [len(c) for c in classes]
    print("per-spot distinct:", per_spot)
    assert per_spot == EXPECT_PER_SPOT, "per-spot 値が不一致 — 移植を確認せよ"

    # 座標圧縮: [係争領域 R][P023\R][P1\R] の順にビットを並べ替える
    P = [0, 0, 0, 0]
    for i in range(4):
        for m in classes[i]:
            P[i] |= m
    P023 = P[0] | P[2] | P[3]
    R = P[1] & P023
    order = bitlist(R) + bitlist(P023 & ~R) + bitlist(P[1] & ~R)
    pos = {b: k for k, b in enumerate(order)}
    SPLIT = bin(R).count("1")
    MASK_R = (1 << SPLIT) - 1

    def compact(m):
        c, i = 0, 0
        while m:
            if m & 1:
                c |= 1 << pos[i]
            m >>= 1
            i += 1
        return c

    C = [[compact(m) for m in cl] for cl in classes]
    pairs23 = [a | b for a in C[2] for b in C[3]]

    totals = []
    for seed in ("seed-A", "seed-B"):
        key = hashlib.blake2b(seed.encode(), digest_size=16).digest()
        t0 = time.time()
        fn = f"records_{seed}.txt"
        with open(fn, "w") as f:
            buf = []
            for m0 in C[0]:
                for u23 in pairs23:
                    U = m0 | u23
                    out = U >> SPLIT
                    h = hashlib.blake2b(
                        out.to_bytes((out.bit_length() + 7) // 8 or 1, "little"),
                        key=key, digest_size=16).hexdigest()
                    buf.append(h + " " + format(U & MASK_R, "x"))
                f.write("\n".join(buf))
                f.write("\n")
                buf = []
        subprocess.run(["sort", "-S", "2G", fn, "-o", fn],
                       env={**os.environ, "LC_ALL": "C"}, check=True)

        total, cache, cur, vals = 0, {}, None, set()

        def flush():
            nonlocal total
            if cur is None:
                return
            if len(vals) == 1:
                v = next(iter(vals))
                c = cache.get(v)
                if c is None:
                    c = len({v | m1 for m1 in C[1]})
                    cache[v] = c
                total += c
            else:
                s = set()
                for v in vals:
                    for m1 in C[1]:
                        s.add(v | m1)
                total += len(s)

        with open(fn) as f:
            for line in f:
                k, v = line.split()
                v = int(v, 16)
                if k != cur:
                    flush()
                    cur, vals = k, {v}
                else:
                    vals.add(v)
        flush()
        totals.append(total)
        print(f"{seed}: EXACT TOTAL = {total:,}  ({time.time()-t0:.0f}s)")

    assert totals[0] == totals[1], "シード間不一致 — ハッシュ衝突の疑い"
    print()
    print(f"解析的厳密値      = {totals[0]:,}")
    print(f"総当たり (Rust)   = {BRUTE_FORCE_RESULT:,}")
    print(f"一致: {totals[0] == BRUTE_FORCE_RESULT}")


if __name__ == "__main__":
    main()
