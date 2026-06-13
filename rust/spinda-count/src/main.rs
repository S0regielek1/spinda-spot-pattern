//! パッチール模様 全数探索
//!
//! 第三世代パッチールの「見た目として区別できる模様」の厳密な数を、
//! 全 2^32 通りの性格値を実描画して数え上げる。
//!
//! 描画仕様は pokeemerald の DRAW_SPINDA_SPOTS (src/pokemon.c) の忠実移植。
//! 2 つの実装を持つ:
//! - render_faithful: 原典マクロの直訳 (4bpp タイルバッファ上で動作)。検証用。
//! - Fast: 「最終画像 = ベース + 4×(4模様の被覆の和集合 ∩ 肌色領域)」という
//!   原典コードから導出した等価表現によるビットマスク実装。本番用。
//!
//! サブコマンド:
//!   verify  - 2 実装の一致確認 (Python テストベクタ + ランダム PID)
//!   perspot - 各スポット単独の異なり数 (先行研究の 256/256/254/237 と照合)
//!   hll     - HyperLogLog による近似全数カウント
//!   exact   - 基数分割 + 128bit ハッシュによる厳密全数カウント

use rayon::prelude::*;
use std::time::Instant;
use xxhash_rust::xxh3::xxh3_64;

/// ベース前面スプライト (anim_front.png 第1フレーム、行優先 64x64 インデックス)
const BASE: &[u8] = include_bytes!("../../../data/base.bin");
/// スポット定義 (x, y, 行マスク u16×16) × 4
const SPOTS_BIN: &[u8] = include_bytes!("../../../data/spots.bin");
/// Python プロトタイプが生成したテストベクタ (PID u32 LE + 64x64 インデックス) × 256
const TEST_VECTORS: &[u8] = include_bytes!("../../../data/test_vectors.bin");

/// スポットが触れうる可視ピクセルの外接矩形 (事前計算済み・起動時に検証する)
const BBOX_X0: usize = 8;
const BBOX_W: usize = 55; // 55 <= 64 なので 1 行が u64 に収まる
const BBOX_H: usize = 49; // y = 0..=48

/// 先行研究 (Bulbapedia) の独立性仮定による上界 256×256×254×237
const PRIOR_UPPER_BOUND: u64 = 3_945_136_128;

/// gSpindaSpotGraphics の 1 エントリ
#[derive(Clone, Copy)]
struct Spot {
    x: u8,
    y: u8,
    rows: [u16; 16],
}

/// spots.bin をパースする
fn parse_spots() -> [Spot; 4] {
    assert_eq!(SPOTS_BIN.len(), 4 * 34, "spots.bin のサイズが想定外");
    std::array::from_fn(|i| {
        let off = i * 34;
        let mut rows = [0u16; 16];
        for r in 0..16 {
            rows[r] = u16::from_le_bytes([SPOTS_BIN[off + 2 + r * 2], SPOTS_BIN[off + 3 + r * 2]]);
        }
        Spot { x: SPOTS_BIN[off], y: SPOTS_BIN[off + 1], rows }
    })
}

// ---------------------------------------------------------------------------
// 忠実移植版 (検証用)
// ---------------------------------------------------------------------------

const TILE_SIZE_4BPP: usize = 32;
/// ゲーム内 gMonSpritesGfxPtr のスロットと同じ 0x2000 バイト。
/// スポット0 の y ニブル 0 で y が u8 巻き戻りして 255 になり、
/// 先頭行がオフセット 7936..8191 (可視フレーム外) に書かれる挙動を吸収する。
const BUFFER_SIZE: usize = 0x2000;

/// 行優先 64x64 → 4bpp タイル形式 (第1フレームのみ、残りはゼロ)
fn linear_to_tiled(base: &[u8]) -> [u8; BUFFER_SIZE] {
    let mut buf = [0u8; BUFFER_SIZE];
    for y in 0..64 {
        for x in 0..64 {
            let idx = base[y * 64 + x];
            let addr = (y / 8) * 256 + (x / 8) * TILE_SIZE_4BPP + (y % 8) * 4 + (x % 8) / 2;
            if x & 1 == 1 {
                buf[addr] |= idx << 4;
            } else {
                buf[addr] |= idx;
            }
        }
    }
    buf
}

/// 4bpp タイル形式の第1フレームを行優先 64x64 に展開する
fn tiled_to_linear(buf: &[u8; BUFFER_SIZE]) -> [u8; 4096] {
    let mut out = [0u8; 4096];
    for y in 0..64 {
        for x in 0..64 {
            let addr = (y / 8) * 256 + (x / 8) * TILE_SIZE_4BPP + (y % 8) * 4 + (x % 8) / 2;
            let b = buf[addr];
            out[y * 64 + x] = if x & 1 == 1 { b >> 4 } else { b & 0x0F };
        }
    }
    out
}

/// DRAW_SPINDA_SPOTS マクロの直訳。可視 64x64 の最終インデックス列を返す。
///
/// 原典との対応:
/// - x, y は u8 (wrapping)、column は s32 (巻き戻りなし)
/// - 下地ニブルが 1..=3 のときだけ +4 する (TRY_DRAW_SPOT_PIXEL)
fn render_faithful(pid: u32, base_tiled: &[u8; BUFFER_SIZE], spots: &[Spot; 4]) -> [u8; 4096] {
    let mut buf = *base_tiled;
    let mut p = pid;
    for spot in spots {
        let x = spot.x.wrapping_add((p & 0x0F) as u8).wrapping_sub(8);
        let mut y = spot.y.wrapping_add(((p & 0xF0) >> 4) as u8).wrapping_sub(8);
        for row in 0..16 {
            let mut spot_pixel_row = spot.rows[row];
            for column in (x as i32)..(x as i32 + 16) {
                let addr = ((column / 8) as usize) * TILE_SIZE_4BPP
                    + ((column % 8) as usize) / 2
                    + (y as usize / 8) * TILE_SIZE_4BPP * 8
                    + (y as usize % 8) * 4;
                if spot_pixel_row & 1 != 0 {
                    if column & 1 != 0 {
                        let v = buf[addr] & 0xF0;
                        if (0x10..=0x30).contains(&v) {
                            buf[addr] += 0x40;
                        }
                    } else {
                        let v = buf[addr] & 0x0F;
                        if (0x01..=0x03).contains(&v) {
                            buf[addr] += 0x04;
                        }
                    }
                }
                spot_pixel_row >>= 1;
            }
            y = y.wrapping_add(1);
        }
        p >>= 8;
    }
    tiled_to_linear(&buf)
}

// ---------------------------------------------------------------------------
// 高速版 (本番用)
// ---------------------------------------------------------------------------

/// スポット 1 つの配置 1 通り (オフセット 16x16 = 256 通りのうちの 1 つ)。
/// rows は bbox 相対 x に左シフト済み・肌色マスク AND 済み・画面外行 (y=-1) はゼロ化済み。
/// 「acc[start + r] |= rows[r]」を 16 行分やるだけで模様 1 つが乗る。
struct Placement {
    start: usize,
    rows: [u64; 16],
}

/// 全スポット×全オフセットの事前計算テーブル
struct Fast {
    /// table[spot][性格値の该当バイト (上位ニブル=dy, 下位ニブル=dx)]
    table: [Vec<Placement>; 4],
}

impl Fast {
    /// ベースとスポット定義からテーブルを構築する
    fn new(base: &[u8], spots: &[Spot; 4]) -> Self {
        // bbox が定数と一致するか検証 (スポット定義が変わったら気付けるように)
        let min_x = spots.iter().map(|s| s.x as i32 - 8).min().unwrap();
        let max_x = spots.iter().map(|s| s.x as i32 + 7 + 15).max().unwrap();
        let min_y = spots.iter().map(|s| s.y as i32 - 8).min().unwrap().max(0);
        let max_y = spots.iter().map(|s| s.y as i32 + 7 + 15).max().unwrap();
        assert_eq!((min_x as usize, min_y as usize), (BBOX_X0, 0), "bbox 原点が想定外");
        assert_eq!(max_x as usize - BBOX_X0 + 1, BBOX_W, "bbox 幅が想定外");
        assert_eq!(max_y as usize + 1, BBOX_H, "bbox 高さが想定外");

        // 肌色 (インデックス 1..=3) の行マスク
        let mut skin = [0u64; BBOX_H];
        for y in 0..BBOX_H {
            for j in 0..BBOX_W {
                if (1..=3).contains(&base[y * 64 + BBOX_X0 + j]) {
                    skin[y] |= 1 << j;
                }
            }
        }

        let table = std::array::from_fn(|i| {
            let spot = &spots[i];
            (0..256usize)
                .map(|byte| {
                    let dx = (byte & 0x0F) as i32;
                    let dy = ((byte >> 4) & 0x0F) as i32;
                    // 画面上の左上座標。x は巻き戻りしない (最小 8)。
                    // y は -1 になりうる (u8 で 255 = 可視フレーム外)。
                    let xs = spot.x as i32 + dx - 8;
                    let ys = spot.y as i32 + dy - 8;
                    let shift = (xs - BBOX_X0 as i32) as u32; // 0..=46
                    let mut rows = [0u64; 16];
                    let start = ys.max(0) as usize;
                    for r in 0..16 {
                        let y = ys + r as i32;
                        if (0..BBOX_H as i32).contains(&y) {
                            // 左シフトで配置し、その行の肌色マスクで事前 AND
                            rows[(y - start as i32) as usize] =
                                ((spot.rows[r as usize] as u64) << shift) & skin[y as usize];
                        }
                    }
                    assert!(start + 15 < BBOX_H, "配置が bbox からはみ出した");
                    Placement { start, rows }
                })
                .collect()
        });
        Fast { table }
    }

    /// 模様 1 つを acc に OR で乗せる
    #[inline(always)]
    fn apply(&self, acc: &mut [u64; BBOX_H], spot: usize, byte: u32) {
        let pl = &self.table[spot][byte as usize];
        let dst = &mut acc[pl.start..pl.start + 16];
        for r in 0..16 {
            dst[r] |= pl.rows[r];
        }
    }

    /// PID 上位 24 ビット分 (スポット 1..3) の部分マスクを作る
    #[inline(always)]
    fn partial_hi(&self, hi: u32) -> [u64; BBOX_H] {
        let mut acc = [0u64; BBOX_H];
        self.apply(&mut acc, 1, hi & 0xFF);
        self.apply(&mut acc, 2, (hi >> 8) & 0xFF);
        self.apply(&mut acc, 3, (hi >> 16) & 0xFF);
        acc
    }

    /// PID 1 つ分の「模様が乗った肌色ピクセル」のビットマスク (見た目の同値キー)
    fn mask(&self, pid: u32) -> [u64; BBOX_H] {
        let mut acc = self.partial_hi(pid >> 8);
        self.apply(&mut acc, 0, pid & 0xFF);
        acc
    }
}

/// マスクをハッシュ入力のバイト列として見る (x86_64 リトルエンディアン前提。
/// エンディアンが変わるとハッシュ値自体は変わるが、数え上げ結果には影響しない)
#[inline(always)]
fn mask_bytes(acc: &[u64; BBOX_H]) -> &[u8] {
    unsafe { std::slice::from_raw_parts(acc.as_ptr().cast::<u8>(), BBOX_H * 8) }
}

// ---------------------------------------------------------------------------
// verify: 実装間の一致確認
// ---------------------------------------------------------------------------

/// マスクからフル画像を再構成する (ベース + 模様ビットに +4)
fn reconstruct(base: &[u8], acc: &[u64; BBOX_H]) -> [u8; 4096] {
    let mut img = [0u8; 4096];
    img.copy_from_slice(base);
    for y in 0..BBOX_H {
        let mut bits = acc[y];
        while bits != 0 {
            let j = bits.trailing_zeros() as usize;
            img[y * 64 + BBOX_X0 + j] += 4;
            bits &= bits - 1;
        }
    }
    img
}

fn cmd_verify(random_count: u64) {
    let spots = parse_spots();
    let base_tiled = linear_to_tiled(BASE);
    let fast = Fast::new(BASE, &spots);

    // 1) Python プロトタイプのテストベクタと忠実移植版の一致
    let entry = 4 + 4096;
    assert_eq!(TEST_VECTORS.len() % entry, 0);
    let n_vec = TEST_VECTORS.len() / entry;
    let mut ng = 0u64;
    for k in 0..n_vec {
        let off = k * entry;
        let pid = u32::from_le_bytes(TEST_VECTORS[off..off + 4].try_into().unwrap());
        let expected = &TEST_VECTORS[off + 4..off + entry];
        let got = render_faithful(pid, &base_tiled, &spots);
        if got != *expected {
            println!("NG: PID {pid:08X} で Python テストベクタと不一致");
            ng += 1;
        }
    }
    println!("テストベクタ {n_vec} 件: 不一致 {ng} 件");

    // 2) 忠実移植版 vs 高速版 (マスクからの再構成画像をピクセル比較)
    let t = Instant::now();
    let mismatch: u64 = (0..random_count)
        .into_par_iter()
        .map(|k| {
            // xorshift64* で決定的にランダム PID を作る
            let mut s = k.wrapping_add(0x9E3779B97F4A7C15);
            s ^= s >> 12;
            s ^= s << 25;
            s ^= s >> 27;
            let pid = (s.wrapping_mul(0x2545F4914F6CDD1D) >> 32) as u32;
            let faithful = render_faithful(pid, &base_tiled, &spots);
            let recon = reconstruct(BASE, &fast.mask(pid));
            u64::from(faithful != recon)
        })
        .sum();
    println!(
        "ランダム PID {random_count} 件 (忠実版 vs 高速版): 不一致 {mismatch} 件 ({:.1}s)",
        t.elapsed().as_secs_f64()
    );

    if ng > 0 || mismatch > 0 {
        std::process::exit(1);
    }
    println!("verify OK");
}

// ---------------------------------------------------------------------------
// perspot: スポット単独の異なり数 (先行研究との照合)
// ---------------------------------------------------------------------------

fn cmd_perspot(base_path: Option<&str>) {
    let spots = parse_spots();
    // ベース差し替え対応 (RS 静止画 front.png などとの比較実験用)
    let alt_base: Vec<u8>;
    let base: &[u8] = match base_path {
        Some(p) => {
            alt_base = std::fs::read(p).expect("ベースファイルを読めない");
            assert_eq!(alt_base.len(), 4096, "ベースは 4096 バイトの 64x64 インデックス列");
            &alt_base
        }
        None => BASE,
    };
    let fast = Fast::new(base, &spots);
    // Bulbapedia の実測値 (積が 3,945,136,128 になる)
    let prior = [256u64, 256, 254, 237];
    let mut product: u64 = 1;
    for i in 0..4 {
        let mut masks: Vec<[u64; BBOX_H]> = (0..256u32)
            .map(|byte| {
                let mut acc = [0u64; BBOX_H];
                fast.apply(&mut acc, i, byte);
                acc
            })
            .collect();
        masks.sort_unstable();
        masks.dedup();
        let n = masks.len() as u64;
        product *= n;
        let mark = if n == prior[i] { "OK" } else { "MISMATCH" };
        println!("スポット{i}: 異なり数 {n} (先行研究 {} → {mark})", prior[i]);
    }
    println!("積 = {product} (先行研究の上界 {PRIOR_UPPER_BOUND})");
}

// ---------------------------------------------------------------------------
// hll: HyperLogLog 近似カウント
// ---------------------------------------------------------------------------

const HLL_P: u32 = 14; // レジスタ 2^14 = 16384 個、標準誤差 ~0.81%

fn cmd_hll() {
    let spots = parse_spots();
    let fast = Fast::new(BASE, &spots);
    let t = Instant::now();

    let regs = (0u32..1 << 24)
        .into_par_iter()
        .fold(
            || vec![0u8; 1 << HLL_P],
            |mut regs, hi| {
                let partial = fast.partial_hi(hi);
                for lo in 0..256u32 {
                    let mut acc = partial;
                    fast.apply(&mut acc, 0, lo);
                    let h = xxh3_64(mask_bytes(&acc));
                    let idx = (h >> (64 - HLL_P)) as usize;
                    let w = h << HLL_P;
                    let rho = if w == 0 { 64 - HLL_P + 1 } else { w.leading_zeros() + 1 };
                    if regs[idx] < rho as u8 {
                        regs[idx] = rho as u8;
                    }
                }
                regs
            },
        )
        .reduce(
            || vec![0u8; 1 << HLL_P],
            |mut a, b| {
                for (x, y) in a.iter_mut().zip(b) {
                    if *x < y {
                        *x = y;
                    }
                }
                a
            },
        );

    let m = (1u64 << HLL_P) as f64;
    let alpha = 0.7213 / (1.0 + 1.079 / m);
    let sum: f64 = regs.iter().map(|&r| (-(r as f64)).exp2()).sum();
    let est = alpha * m * m / sum;
    println!("HLL 推定ユニーク数: {est:.0} (標準誤差 ~0.81% = ±{:.0})", est * 0.0081);
    println!("先行研究の上界:     {PRIOR_UPPER_BOUND}");
    println!("比率: {:.4} (経過 {:.1}s)", est / PRIOR_UPPER_BOUND as f64, t.elapsed().as_secs_f64());
}

// ---------------------------------------------------------------------------
// exact: 基数分割による厳密カウント
// ---------------------------------------------------------------------------

fn cmd_exact(passes: u32, seed: u64) {
    assert!(passes.is_power_of_two() && passes <= 256, "passes は 2 のべき乗 (<=256)");
    let bits = passes.trailing_zeros();
    let spots = parse_spots();
    let fast = Fast::new(BASE, &spots);
    let t_all = Instant::now();
    let mut total: u64 = 0;

    for pass in 0..passes {
        let t = Instant::now();
        // 全 PID を再描画し、128bit ハッシュの上位 bits ビットが pass のものだけ収集
        let mut hashes: Vec<u128> = (0u32..1 << 24)
            .into_par_iter()
            .fold(Vec::new, |mut v, hi| {
                let partial = fast.partial_hi(hi);
                for lo in 0..256u32 {
                    let mut acc = partial;
                    fast.apply(&mut acc, 0, lo);
                    // seed を変えると全く別のハッシュ族になる (検算用)
                    let h = xxhash_rust::xxh3::xxh3_128_with_seed(mask_bytes(&acc), seed);
                    let part = if bits == 0 { 0 } else { (h >> (128 - bits)) as u32 };
                    if part == pass {
                        v.push(h);
                    }
                }
                v
            })
            .reduce(Vec::new, |mut a, mut b| {
                if a.len() < b.len() {
                    std::mem::swap(&mut a, &mut b);
                }
                a.append(&mut b);
                a
            });

        let collected = hashes.len();
        hashes.par_sort_unstable();
        let uniq = if hashes.is_empty() {
            0
        } else {
            1 + hashes.par_windows(2).filter(|w| w[0] != w[1]).count() as u64
        };
        total += uniq;
        println!(
            "パス {:>3}/{passes}: 収集 {collected} 件 → ユニーク {uniq} 件 (累計 {total}, {:.1}s)",
            pass + 1,
            t.elapsed().as_secs_f64()
        );
    }

    println!("=== 結果 ===");
    println!("厳密ユニーク数:   {total}");
    println!("先行研究の上界:   {PRIOR_UPPER_BOUND}");
    println!("差分 (重なり縮退): {}", PRIOR_UPPER_BOUND as i64 - total as i64);
    println!("総経過時間: {:.1}s", t_all.elapsed().as_secs_f64());
    println!("(参考: 128bit ハッシュ使用。4.3e9 件での衝突確率は ~1e-20 で無視できる)");
}

// ---------------------------------------------------------------------------

fn main() {
    let args: Vec<String> = std::env::args().collect();
    match args.get(1).map(String::as_str) {
        Some("verify") => {
            let n = args.get(2).map_or(1_000_000, |s| s.parse().expect("数値を指定"));
            cmd_verify(n);
        }
        Some("perspot") => cmd_perspot(args.get(2).map(String::as_str)),
        Some("hll") => cmd_hll(),
        Some("exact") => {
            let passes = args.get(2).map_or(16, |s| s.parse().expect("数値を指定"));
            let seed = args.get(3).map_or(0, |s| s.parse().expect("数値を指定"));
            cmd_exact(passes, seed);
        }
        _ => {
            eprintln!("使い方: spinda-count <verify [N] | perspot | hll | exact [passes]>");
            std::process::exit(2);
        }
    }
}
