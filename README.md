# Spinda Pattern Count — Reproducibility Guide

> Exhaustive count of the visually distinct spot patterns a Generation III Spinda can display.
>
> **Result: `3,880,873,934` distinct appearances.**

youtube:https://youtu.be/HROxOfwWU1w

*(日本語版は下にあります — [日本語 README](#日本語--追試ガイド))*

---

## What we asked

Spinda's spots are placed from its 32-bit personality value (PID), so the naïve
upper bound is 2³² = 4,294,967,296. But many PIDs produce the *same* picture: a
spot can clip off the body, spots can overlap and merge, and a spot only lands on
"skin" palette indices. The question this repo answers is the exact one: **how many
visually distinct Spinda exist in Generation III (Ruby/Sapphire, Emerald, FireRed/
LeafGreen)?** We render every one of the 2³² PIDs through a faithful port of the
game's `DRAW_SPINDA_SPOTS` routine, deduplicate the final pixels, and count. The
answer is **3,880,873,934** — about 90.36 % of the theoretical maximum, and the
precise figure behind the Pokédex's "less than one in four billion" claim.

The full write-up of definitions, the per-spot breakdown, and why all three Gen III
games share the same number lives in [`results.md`](results.md).

## Verification chain (three independent paths agree)

The number is trusted because three methods that share no code arrive at it:

1. **Two independent renderers agree.** A Python transliteration of the
   `DRAW_SPINDA_SPOTS` macro (operating on a 4bpp tile buffer) and a faithful Rust
   port agree pixel-for-pixel on a deterministic test set, and the production
   bitmask renderer is reconstructed back to full images and checked against the
   faithful renderer on **100,000,000** random PIDs.
2. **A second, independent hash agrees.** The full 2³² exhaustive count was re-run
   with a different xxh3-128 hash family (changed seed) and a different pass count
   (32 instead of 16), yielding the identical exact value. For 4.3×10⁹ keys under a
   128-bit hash, a single collision has probability ≈ 3×10⁻²⁰; agreement across two
   independent hashes removes even that.
3. **An analytic decomposition agrees.** [`tools/analytic_count.py`](tools/analytic_count.py)
   mechanizes inclusion–exclusion: it enumerates the spot-0/2/3 class products and
   counts the exact distinct compositions over spot 1's mobile region — a completely
   different code path from brute force — and lands on the same 3,880,873,934.

Two cross-checks back these up: the per-spot distinct counts `{253, 256, 236, 256}`
match Bulbapedia's position-based `{254, 256, 237, 256}` by exactly the predicted
"at most one smaller" relation, and the 16-pass collection total equals exactly 2³²
(every PID counted exactly once).

## Requirements

| | Version / dependency |
|---|---|
| **Rust** (main engine) | Rust 1.96, x86_64. Crates: `rayon = "1"`, `xxhash-rust = "0.8"` (xxh3). `lto = true`. |
| **Python** (asset extraction, prototype, analytic) | Python 3.11+, `pillow`. The analytic and asset steps fetch raw files from the decomp repos over the network. |
| **Decomp sources** | `pokeemerald`, `pokeruby`, `pokefirered` sparse-cloned locally (analysis/verification only — see *Data handling*). |

Measured on Windows 11 / Ryzen 7 9700X (8C/16T).

## How to run (exact commands, in order)

```powershell
# 1. Extract assets (requires pokeemerald / pokeruby / pokefirered sparse-cloned)
python tools\extract_assets.py    # writes data\base.bin, palette.bin, spots.bin; cross-checks against ROM-derived bytes

# 2. Generate test vectors + sample PNGs (eyeball check)
python tools\proto_render.py      # writes data\test_vectors.bin, out\*.png

# 3. Rust verification + production count
cd rust\spinda-count
cargo build --release
.\target\release\spinda-count.exe verify 100000000    # renderer agreement on 100M PIDs
.\target\release\spinda-count.exe perspot             # per-spot distinct counts
.\target\release\spinda-count.exe hll                 # HyperLogLog approximation
.\target\release\spinda-count.exe exact 16            # exact count, 16 passes
.\target\release\spinda-count.exe exact 32 1234567890123   # re-count with a different hash/seed

# 4. (optional) Independent analytic path — no brute force
python tools\analytic_count.py    # inclusion–exclusion; requires pillow + network
```

## Measured runtimes

Quoted from the verification run (Ryzen 7 9700X, 16 threads):

- **HLL approximation** (renders all 2³², estimates): **10.6 s** → estimate
  3,896,771,644, within 0.5 σ of the exact value.
- **Exact count, 16 passes** (16 × 2³² renders): **258.4 s**.
- **Re-count, seed 1234567890123, 32 passes** (independent hash, full 2³²):
  **500.4 s** — same exact value `3,880,873,934`, collection total `4,294,967,296 = 2³²`.
- Per-PID render+hash ≈ **40 ns** (single-thread equivalent).
- **Analytic path** ≈ a few minutes (enumeration ~30 s + sort/aggregate ~50 s × 2 seeds).

## Data handling

- `pokeemerald`, `pokeruby`, and `pokefirered` are referenced **for analysis and
  verification only**. They are re-clonable from [pret](https://github.com/pret) and
  are **not redistributed** here.
- The extracted assets under `data/` (`base.bin`, `palette.bin`, `spots.bin`, …) are
  **ROM-derived** and are **not redistributed**. They are regenerated locally by
  `tools/extract_assets.py`.
- Both are excluded via [`.gitignore`](.gitignore):

  ```gitignore
  pokeemerald/        # re-cloneable, not redistributed
  pokeruby/
  pokefirered/
  data/               # ROM-derived, regenerable via tools/extract_assets.py
  out/                # generated images
  rust/spinda-count/target/
  ```

  > One trap worth flagging (full detail in `results.md`): `gbagfx`'s 1bpp
  > conversion maps **bit15 of the u16 to the left-most PNG pixel**, while the game
  > draws from bit0 — so a PNG read naïvely gives a left-right *mirrored* pattern.
  > We cross-checked spot shapes against pokeruby's raw `spot_*.bin` (actual ROM
  > bytes) to pin this down.

## License & attribution

- **Original code in this repo** (`rust/spinda-count/`, `tools/*.py`) is released
  under the **MIT License**.
- The referenced game logic and assets (`DRAW_SPINDA_SPOTS`, sprite/spot graphics,
  palettes, coordinate tables) belong to **Nintendo / Creatures Inc. / GAME FREAK**.
  Nothing ROM-derived is included or redistributed; this project only studies the
  publicly documented decompilations.
- The decompilation projects **pokeemerald / pokeruby / pokefirered** are the work
  of the [pret](https://github.com/pret) team and are used under their respective
  licenses.

## Prior work & credits

- **[Bulbapedia — Spinda § Spots](https://bulbapedia.bulbagarden.net/wiki/Spinda)**
  for the position-based per-spot counts and the "less than one in four billion"
  framing that this study sharpens.
- **Lettuce Leaf(https://www.youtube.com/watch?v=J1g58JP0BfE)** — whose video on counting Spinda patterns prompted this
  independent recount.
- **[Spinda Painter](https://yenkozinda.github.io/SpindaPainter/)** — interactive
  PID↔pattern reference used for visual spot-checks.

---
<a name="日本語--追試ガイド"></a>

# パッチール模様カウント — 追試ガイド

> 第三世代のパッチールが表示しうる「見た目として区別できる模様」の全数探索。
>
> **結果: `3,880,873,934` 通り。**

## 何を求めたか
youtubeで経緯と解説をしております。
https://youtu.be/HROxOfwWU1w
パッチールの模様は 32bit の性格値（PID）から配置されるので、素朴な上界は
2³² = 4,294,967,296 です。しかし多くの PID が *同じ* 絵を生みます。模様は
体からはみ出してクリップされ、模様どうしが重なって融合し、模様は「肌色」
パレットインデックスの上にしか乗りません。本リポジトリが答えるのはまさに
厳密な問いです。**第三世代（ルビー・サファイア、エメラルド、FRLG）で、見た目
として区別できるパッチールは何通りあるか？** ゲームの `DRAW_SPINDA_SPOTS`
ルーチンを忠実に移植し、2³² 個すべての PID を描画し、最終ピクセルで重複除去
して数えました。答えは **3,880,873,934** — 理論最大の約 90.36 %、そして図鑑
の「40億分の1未満」という記述の正確な数字です。

定義・スポット別の内訳・第三世代3作品で値が同一になる根拠の詳細は
[`results.md`](results.md) にあります。

## 検証チェーン（独立3経路の三方一致）

コードを共有しない3つの方法が同じ値に到達するため、この数字を信頼できます。

1. **独立2実装の一致。** `DRAW_SPINDA_SPOTS` マクロを直訳した Python 版
   （4bpp タイルバッファ上で動作）と、Rust 忠実移植版が、決定的なテスト集合で
   全ピクセル一致。さらに本番用ビットマスク描画の出力からフル画像を再構成し、
   ランダム **1億 PID** で忠実版とピクセル完全一致。
2. **別ハッシュの一致。** 全 2³² の厳密カウントを、別の xxh3-128 ハッシュ族
   （シード変更）・別の分割数（16 → 32）で再実行し、同一の厳密値を得た。
   128bit ハッシュで 4.3×10⁹ 件なら単一衝突の確率は ≈ 3×10⁻²⁰。独立2ハッシュ
   での一致がそれすら排除します。
3. **解析的分解の一致。** [`tools/analytic_count.py`](tools/analytic_count.py)
   が包除原理を機械化します。スポット 0/2/3 のクラス組を列挙し、スポット1の
   可動領域上で厳密な異なり合成数を数える — 総当たりとは完全に別の経路 — で、
   同じ 3,880,873,934 に着地します。

補強の2つの照合: スポット別異なり数 `{253, 256, 236, 256}` は Bulbapedia の
位置ベース `{254, 256, 237, 256}` と理論どおり「高々1小さい」の関係で一致し、
16 パスの収集件数合計はちょうど 2³²（全 PID がちょうど1回ずつ）です。

## 必要環境

| | バージョン / 依存 |
|---|---|
| **Rust**（メインエンジン） | Rust 1.96, x86_64。クレート: `rayon = "1"`, `xxhash-rust = "0.8"`（xxh3）。`lto = true`。 |
| **Python**（抽出・試作・解析） | Python 3.11+, `pillow`。解析・抽出ステップは decomp リポジトリから生ファイルをネットワーク取得します。 |
| **decomp ソース** | `pokeemerald` / `pokeruby` / `pokefirered` をローカルにスパースクローン（解析・検証目的のみ — *データの扱い* 参照）。 |

実測環境: Windows 11 / Ryzen 7 9700X (8C/16T)。

## 実行手順（実際のコマンド・順番通り）

```powershell
# 1. アセット抽出（pokeemerald / pokeruby / pokefirered をスパースクローン済みであること）
python tools\extract_assets.py    # data\base.bin, palette.bin, spots.bin を生成し、ROM 由来バイトと照合

# 2. テストベクタ生成 + サンプル PNG（目視確認）
python tools\proto_render.py      # data\test_vectors.bin, out\*.png を生成

# 3. Rust 検証 + 本番カウント
cd rust\spinda-count
cargo build --release
.\target\release\spinda-count.exe verify 100000000    # 1億 PID で描画実装の一致検証
.\target\release\spinda-count.exe perspot             # スポット別異なり数
.\target\release\spinda-count.exe hll                 # HyperLogLog 近似カウント
.\target\release\spinda-count.exe exact 16            # 厳密カウント（16 パス）
.\target\release\spinda-count.exe exact 32 1234567890123   # 別ハッシュ/シードで検算

# 4. （任意）独立した解析経路 — 総当たり不使用
python tools\analytic_count.py    # 包除原理。pillow + ネットワークが必要
```

## 実行時間の実測値

検算実行（Ryzen 7 9700X, 16 スレッド）のログから引用:

- **HLL 近似**（全 2³² 描画 + 推定）: **10.6 秒** → 推定 3,896,771,644、
  厳密値の 0.5 σ 以内。
- **厳密カウント・16 パス**（16 × 2³² 描画）: **258.4 秒**。
- **検算・シード 1234567890123・32 パス**（独立ハッシュ、全 2³²）:
  **500.4 秒** — 同一の厳密値 `3,880,873,934`、収集件数合計 `4,294,967,296 = 2³²`。
- 1 PID あたりの描画 + ハッシュ ≈ **40 ns**（シングルスレッド換算）。
- **解析経路** ≈ 数分（列挙 ~30 秒 + ソート・集計 ~50 秒 × 2 シード）。

## データの扱い

- `pokeemerald` / `pokeruby` / `pokefirered` は **解析・検証目的のみ** で参照します。
  [pret](https://github.com/pret) から再クローン可能であり、本リポジトリでは
  **再配布しません**。
- `data/` 以下の抽出アセット（`base.bin`, `palette.bin`, `spots.bin` …）は
  **ROM 由来** であり **再配布しません**。`tools/extract_assets.py` でローカル
  再生成できます。
- いずれも [`.gitignore`](.gitignore) で除外しています:

  ```gitignore
  pokeemerald/        # 再クローン可能・再配布しない
  pokeruby/
  pokefirered/
  data/               # ROM 由来・extract_assets.py で再生成可能
  out/                # 生成画像
  rust/spinda-count/target/
  ```

  > 注意すべき罠を1つ（詳細は `results.md`）: `gbagfx` の 1bpp 変換は
  > **u16 の bit15 = PNG 左端ピクセル** に対応しますが、ゲームは bit0 から
  > 描きます。そのため PNG を素直に読むと模様が左右 *反転* します。スポット
  > 形状を pokeruby の生 `spot_*.bin`（ROM 実バイト）と照合して確定しました。

## ライセンス・帰属

- **本リポジトリの自作コード**（`rust/spinda-count/`, `tools/*.py`）は
  **MIT ライセンス** で公開します。
- 参照したゲームロジック・アセット（`DRAW_SPINDA_SPOTS`、スプライト/模様
  グラフィックス、パレット、座標テーブル）は **任天堂 / 株式会社クリーチャーズ /
  株式会社ゲームフリーク** に帰属します。ROM 由来データは一切含めず・再配布せず、
  本プロジェクトは公開された decompilation を解析するのみです。
- decompilation プロジェクト **pokeemerald / pokeruby / pokefirered** は
  [pret](https://github.com/pret) チームの成果であり、それぞれのライセンスに
  従って利用しています。

## 先行研究・クレジット

- **[Bulbapedia — Spinda § Spots](https://bulbapedia.bulbagarden.net/wiki/Spinda)**
  — 位置ベースのスポット別カウントと「40億分の1未満」という枠組み。本研究は
  これを精密化したものです。
- **Lettuce Leaf 氏(https://www.youtube.com/watch?v=J1g58JP0BfE)** — パッチール模様の数え上げに関する動画が、この独立な
  再カウントのきっかけになりました。
- **[Spinda Painter](https://yenkozinda.github.io/SpindaPainter/)** — PID↔模様
  の対話的リファレンス。目視照合に使用しました。
