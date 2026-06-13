"""pokeemerald からパッチール描画に必要なアセットを抽出するスクリプト。

抽出するもの:
- ベース前面スプライト (64x64, インデックスカラー) -> data/base.bin (4096 バイト, 行優先で 1 ピクセル 1 バイト)
- パレット (JASC-PAL 形式) -> data/palette.bin (16 色 x RGB 3 バイト = 48 バイト)
- スポット形状 4 つ (16x16 の 1bpp マスク) -> data/spots.bin (スポットごとに x, y, 行マスク u16 x 16 = 34 バイト, 計 136 バイト)

スポット行マスクのビット順は pokeemerald の DRAW_SPINDA_SPOTS と同じ
「bit0 = 左端のピクセル」(spotPixelRow & 1 が column = x に対応) とする。
"""

from pathlib import Path
import struct

from PIL import Image

# パス定義
ROOT: Path = Path(__file__).resolve().parent.parent
POKEEMERALD: Path = ROOT / "pokeemerald"
GFX_DIR: Path = POKEEMERALD / "graphics" / "pokemon" / "spinda"
DATA_DIR: Path = ROOT / "data"

# pokeemerald の src/pokemon.c にある gSpindaSpotGraphics の基準座標
SPOT_BASE_COORDS: list[tuple[int, int]] = [(16, 7), (40, 8), (22, 25), (34, 26)]


def extract_base_sprite() -> bytes:
    """anim_front.png から第1フレーム (64x64) のインデックス列を抽出する。

    anim_front.png は 64x128 (2 フレーム縦積み)。エメラルドのパッチールは
    1 フレームのみ使用するため、念のため 2 フレームが同一かも確認する。
    """
    im = Image.open(GFX_DIR / "anim_front.png")
    if im.mode != "P" or im.size != (64, 128):
        raise ValueError(f"anim_front.png の形式が想定外: mode={im.mode}, size={im.size}")

    pixels = list(im.getdata())  # 行優先 8192 個のパレットインデックス
    frame1 = pixels[: 64 * 64]
    frame2 = pixels[64 * 64 :]
    print(f"フレーム1 == フレーム2: {frame1 == frame2}")

    # 静止画 front.png とも照合しておく (世代内で同一スプライトのはず)
    im_front = Image.open(GFX_DIR / "front.png")
    front_pixels = list(im_front.getdata())
    print(f"フレーム1 == front.png: {frame1 == front_pixels}")

    if max(frame1) > 15:
        raise ValueError("4bpp の範囲 (0..15) を超えるインデックスがある")
    return bytes(frame1)


def extract_palette() -> bytes:
    """normal.pal (JASC-PAL 形式) から 16 色の RGB を読み出す。"""
    lines = (GFX_DIR / "normal.pal").read_text(encoding="ascii").splitlines()
    if lines[0] != "JASC-PAL" or lines[2] != "16":
        raise ValueError(f"JASC-PAL 形式でない: {lines[:3]}")
    out = bytearray()
    for line in lines[3 : 3 + 16]:
        r, g, b = (int(v) for v in line.split())
        out += bytes((r, g, b))
    return bytes(out)


def extract_spots() -> bytes:
    """spot_0..3 の PNG を 16 行の u16 マスクに変換する。

    PNG はインデックス 0 = 白 (背景), 1 = 黒 (スポット部分)。

    ビット順に注意: gbagfx の 1bpp 変換 (-plain -data_width 2) は
    「u16 の bit15 = PNG の左端ピクセル」で詰める。一方、原典マクロは
    bit0 (= PNG の右端) から描き始めるので、ゲーム内の模様は PNG を
    左右反転した形になる。ここでは「bit0 = 描画時の左端」の値、
    すなわち bit(15 - col) に PNG の col を対応させた u16 を保存する。
    これは pokeruby の graphics/spinda_spots/spot_*.bin (ROM 実バイト) と
    一致するはずで、main() で照合する。
    """
    out = bytearray()
    for i, (bx, by) in enumerate(SPOT_BASE_COORDS):
        im = Image.open(GFX_DIR / "spots" / f"spot_{i}.png")
        if im.size != (16, 16):
            raise ValueError(f"spot_{i}.png のサイズが想定外: {im.size}")
        px = list(im.getdata())
        rows: list[int] = []
        for row in range(16):
            mask = 0
            for col in range(16):
                if px[row * 16 + col] == 1:
                    mask |= 1 << (15 - col)
            rows.append(mask)
        out += struct.pack("<BB16H", bx, by, *rows)
        # 目視確認用にマスクを表示
        print(f"spot_{i} (base x={bx}, y={by}):")
        for mask in rows:
            print("  " + "".join("#" if (mask >> c) & 1 else "." for c in range(16)))
    return bytes(out)


def report_bbox_and_skin(base: bytes) -> None:
    """スポットが触れうる可視領域の外接矩形と、その中の肌色ピクセル数を報告する。

    各スポットのオフセットは (ニブル - 8) なので -8..+7。スポットは 16x16。
    y はゲーム内で u8 巻き戻りするが、可視フレームは y 0..63 のみ。
    """
    min_x = min(bx - 8 for bx, _ in SPOT_BASE_COORDS)
    max_x = max(bx + 7 + 15 for bx, _ in SPOT_BASE_COORDS)
    min_y = max(0, min(by - 8 for _, by in SPOT_BASE_COORDS))  # y=-1 は画面外
    max_y = max(by + 7 + 15 for _, by in SPOT_BASE_COORDS)
    print(f"bounding box: x {min_x}..{max_x}, y {min_y}..{max_y} "
          f"({max_x - min_x + 1} x {max_y - min_y + 1})")

    skin = sum(
        1
        for y in range(min_y, max_y + 1)
        for x in range(min_x, max_x + 1)
        if 1 <= base[y * 64 + x] <= 3
    )
    print(f"bbox 内の肌色ピクセル数 (インデックス 1..3): {skin}")


def verify_against_ruby_bins(spots: bytes) -> None:
    """抽出したスポットマスクを pokeruby の ROM 実バイト (.bin) と照合する。

    ルビサファとエメラルドのスポット形状データは同一なので、
    全 64 行が一致しなければ抽出 (ビット順) が間違っている。
    """
    ruby_dir = ROOT / "pokeruby" / "graphics" / "spinda_spots"
    for i in range(4):
        ruby = (ruby_dir / f"spot_{i}.bin").read_bytes()
        mine = spots[i * 34 + 2 : i * 34 + 34]
        if ruby != mine:
            raise ValueError(f"spot_{i}: pokeruby の .bin と不一致 (ビット順の誤り)")
    print("pokeruby の spot_*.bin と全一致 (ビット順 OK)")


def main() -> None:
    """アセットを抽出して data/ に保存する。"""
    DATA_DIR.mkdir(exist_ok=True)
    base = extract_base_sprite()
    (DATA_DIR / "base.bin").write_bytes(base)
    palette = extract_palette()
    (DATA_DIR / "palette.bin").write_bytes(palette)
    spots = extract_spots()
    verify_against_ruby_bins(spots)
    (DATA_DIR / "spots.bin").write_bytes(spots)
    report_bbox_and_skin(base)
    print("抽出完了: data/base.bin, data/palette.bin, data/spots.bin")


if __name__ == "__main__":
    main()
