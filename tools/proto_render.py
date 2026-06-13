"""パッチール模様描画の Python プロトタイプ (pokeemerald 忠実移植版)。

pokeemerald の DRAW_SPINDA_SPOTS マクロ (src/pokemon.c) を 4bpp タイル形式
バッファ上でそのまま直訳した「原典に最も近い」実装。

- バッファは 0x2000 バイト (ゲーム内 gMonSpritesGfxPtr のスロットと同じ大きさ)。
  スポット0 の y ニブルが 0 のとき y = -1 が u8 巻き戻りで 255 になり、
  先頭行が可視フレーム外 (オフセット 7936..8191) に書かれる挙動も再現される。
- 見た目 = 第1フレーム (先頭 0x800 バイト) を 64x64 に展開したもの。

用途:
1. サンプル PID の PNG 出力 (目視確認用)
2. Rust 実装と突き合わせるテストベクタの生成
"""

from pathlib import Path
import argparse
import struct

from PIL import Image

ROOT: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = ROOT / "data"
OUT_DIR: Path = ROOT / "out"

TILE_SIZE_4BPP: int = 32  # 8x8 タイル 1 枚 = 32 バイト
BUFFER_SIZE: int = 0x2000  # ゲーム内スプライトバッファと同サイズ


def load_spots() -> list[tuple[int, int, list[int]]]:
    """data/spots.bin から (基準x, 基準y, 行マスク16個) のリストを読む。"""
    raw = (DATA_DIR / "spots.bin").read_bytes()
    spots: list[tuple[int, int, list[int]]] = []
    for i in range(4):
        bx, by, *rows = struct.unpack_from("<BB16H", raw, i * 34)
        spots.append((bx, by, list(rows)))
    return spots


def load_base_tiled() -> bytes:
    """ベーススプライト (行優先インデックス) を 4bpp タイル形式に変換して返す。

    第1フレームのみ実画像、残り (0x800..0x1FFF) はゲーム同様ゼロ埋め。
    4bpp は 1 バイトに 2 ピクセル: 下位ニブル = 偶数 x、上位ニブル = 奇数 x。
    """
    base = (DATA_DIR / "base.bin").read_bytes()
    buf = bytearray(BUFFER_SIZE)
    for y in range(64):
        for x in range(64):
            idx = base[y * 64 + x]
            addr = (y // 8) * 256 + (x // 8) * TILE_SIZE_4BPP + (y % 8) * 4 + (x % 8) // 2
            if x & 1:
                buf[addr] |= idx << 4
            else:
                buf[addr] |= idx
    return bytes(buf)


def draw_spinda_spots(personality: int, dest: bytearray,
                      spots: list[tuple[int, int, list[int]]]) -> None:
    """DRAW_SPINDA_SPOTS マクロの直訳。dest (4bpp タイル形式) を直接書き換える。

    原典との対応:
    - x, y は u8 (& 0xFF で巻き戻り)
    - column は s32 (巻き戻りなし)
    - 下地ニブルが 1..3 のときだけ +4 (TRY_DRAW_SPOT_PIXEL)
    """
    p = personality
    for bx, by, rows in spots:
        x = (bx + (p & 0x0F) - 8) & 0xFF
        y = (by + ((p & 0xF0) >> 4) - 8) & 0xFF
        for row in range(16):
            spot_pixel_row = rows[row]
            for column in range(x, x + 16):
                addr = ((column // 8) * TILE_SIZE_4BPP + (column % 8) // 2
                        + (y // 8) * TILE_SIZE_4BPP * 8 + (y % 8) * 4)
                if spot_pixel_row & 1:
                    if column & 1:
                        # 上位ニブル (奇数 x)
                        if 0x10 <= (dest[addr] & 0xF0) <= 0x30:
                            dest[addr] += 0x40
                    else:
                        # 下位ニブル (偶数 x)
                        if 0x01 <= (dest[addr] & 0x0F) <= 0x03:
                            dest[addr] += 0x04
                spot_pixel_row >>= 1
            y = (y + 1) & 0xFF
        p >>= 8


def tiled_to_linear(buf: bytes) -> bytes:
    """4bpp タイル形式の第1フレームを 64x64 の行優先インデックス列に展開する。"""
    out = bytearray(64 * 64)
    for y in range(64):
        for x in range(64):
            addr = (y // 8) * 256 + (x // 8) * TILE_SIZE_4BPP + (y % 8) * 4 + (x % 8) // 2
            b = buf[addr]
            out[y * 64 + x] = (b >> 4) if (x & 1) else (b & 0x0F)
    return bytes(out)


def render(personality: int, base_tiled: bytes,
           spots: list[tuple[int, int, list[int]]]) -> bytes:
    """PID 1 つ分を描画し、可視 64x64 のインデックス列を返す。"""
    buf = bytearray(base_tiled)
    draw_spinda_spots(personality, buf, spots)
    return tiled_to_linear(buf)


def save_png(indices: bytes, path: Path) -> None:
    """インデックス列をパレット適用の上 4 倍拡大 PNG で保存する。"""
    pal = (DATA_DIR / "palette.bin").read_bytes()
    im = Image.frombytes("P", (64, 64), indices)
    im.putpalette(pal + bytes(768 - len(pal)))
    im = im.resize((256, 256), Image.NEAREST)
    im.save(path)


def main() -> None:
    """サンプル PID の PNG と、Rust 照合用テストベクタを出力する。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--pids", nargs="*", default=None,
                        help="PNG 出力する PID (16進)。省略時は既定セット")
    args = parser.parse_args()

    OUT_DIR.mkdir(exist_ok=True)
    spots = load_spots()
    base_tiled = load_base_tiled()

    # 既定サンプル: 全0 / 全F / 中央配置 (ニブル8=オフセット0) / 適当な値
    default_pids = [0x00000000, 0xFFFFFFFF, 0x88888888, 0x12345678, 0xDEADBEEF]
    pids = ([int(s, 16) for s in args.pids] if args.pids else default_pids)
    for pid in pids:
        indices = render(pid, base_tiled, spots)
        path = OUT_DIR / f"pid_{pid:08X}.png"
        save_png(indices, path)
        print(f"保存: {path}")

    # ベース (模様なし) も比較用に出力
    save_png(tiled_to_linear(base_tiled), OUT_DIR / "base.png")

    # Rust 照合用テストベクタ: 決定的な擬似乱数で 256 個の PID を選び、
    # PID(u32 LE) + 64x64 インデックス列 を連結して書き出す
    rng_state = 0x1234ABCD
    test_pids: list[int] = [0x00000000, 0xFFFFFFFF, 0x88888888, 0x00000010,
                            0x00000001, 0x80000000, 0x08080808, 0xF0F0F0F0]
    for _ in range(248):
        # xorshift32 (決定的)
        rng_state ^= (rng_state << 13) & 0xFFFFFFFF
        rng_state ^= rng_state >> 17
        rng_state ^= (rng_state << 5) & 0xFFFFFFFF
        test_pids.append(rng_state)
    with (DATA_DIR / "test_vectors.bin").open("wb") as f:
        for pid in test_pids:
            f.write(struct.pack("<I", pid))
            f.write(render(pid, base_tiled, spots))
    print(f"テストベクタ {len(test_pids)} 件を data/test_vectors.bin に出力")


if __name__ == "__main__":
    main()
