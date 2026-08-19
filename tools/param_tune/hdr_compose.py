#!/usr/bin/env python3
"""param_sweep.py で同一の静止シーンを露光違いで撮った複数のdepth（+IR）フレームを、
画素ごとに選択して1枚のdepthに合成する、カメラ内蔵HDR/WDRを使わないオフラインの
後処理ツール。

背景・カメラ内蔵HDR/WDRとの違い:
    NYX660のHDR/WDRモード（scSetHDRModeEnabled等）は、1フレーム期間の中で複数の
    サブ露光を切り替えて生データ（相関値/位相）を取得し、画素ごとにどのサブ露光を
    使うかをファームウェアが選んで1枚のdepthフレームとして出力する。この合成
    ロジックの詳細はSDK非公開で、こちらから中身を作り込むことはできない
    （param_sweep.py --param hdr_mode --values 0,1 でON/OFFの比較しかできない理由）。

    本スクリプトはその代わりに、静止シーンを手動で複数の露光時間（tof_exposure）で
    連続撮影した「計算済みのdepth（+IR）」を画素ごとに選択/合成することで、HDR的な
    効果を後処理で近似する。カメラ内蔵HDRは生データレベルでの合成なので理論上は
    有利だが、SDKが公開していない合成ロジックを自分で設計・検証できる利点がある。

    ただし、露光ごとの撮影の間（param_sweep.pyのwarmup+pause分、数百ms〜秒オーダー）
    にシーンが動くと画素がズレて合成が破綻するため、**完全に静止したシーン**を撮った
    セッションでのみ有効。

前提となる撮影:
    python3 tools/param_tune/param_sweep.py --param tof_exposure --values 1000:8000:1000 \\
        --save-ir --no-auto-reference
    のように、静止シーンを対象に --save-ir を付けて撮影しておくこと
    （ir_aware モードを使わない場合は --save-ir は無くても動く）。

合成モード（--mode）:
    first_valid（既定）: 露光を短い順に走査し、画素ごとに「depth>0（有効）の
        最初の露光」を採用する。露光が短いほど近距離・高反射率物体の飽和を
        避けやすいため、優先度を短い方から高くしている。
    ir_aware: first_valid に加えて、IRフレームの輝度が --ir-saturation 以上
        （飽和寄り＝信頼できない）の画素は採用候補から除外する。全露光で
        除外された画素は2パス目でIR条件を無視した first_valid 相当のフォールバック
        で埋める（無効なままより、飽和覚悟でも埋めた方が有用なため）。
        --save-ir で撮影したIRフレームが必要。

使い方:
    # 区間（start:stop:step）で指定
    python3 tools/param_tune/hdr_compose.py <session_dir> --values 1000:8000:1000

    # 値を列挙して指定
    python3 tools/param_tune/hdr_compose.py <session_dir> --values 1000,3000,5000,8000

    # IRの飽和判定を使う場合（--save-irで撮影したセッションのみ）
    python3 tools/param_tune/hdr_compose.py <session_dir> --values 1000:8000:1000 \\
        --mode ir_aware --ir-saturation 250

保存先:
    <session_dir>/hdr_compose_<tag>/
        composed_depth.png            合成後 depth（16bit, mm）
        composed_depth_colormap.png
        source_map.png                画素ごとにどの露光（値）が採用されたかの可視化
        comparison.png                各露光のdepth_colormapと合成結果を並べた比較画像
        metadata.json                 使用した露光値・モード・各露光の採用画素数など
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / 'nyx660_script'))

from common import parse_values, cast_value, combo_dirname, combo_label, result_filename, find_result_file, build_montage
from utils import make_depth_colormap

# source_map可視化用の固定パレット（BGR）。露光インデックスごとに割り当てる。
# 10色を超える場合はcv2.COLORMAP_HSVで自動生成する。
_PALETTE = [
    (255, 87, 34), (76, 175, 80), (33, 150, 243), (255, 193, 7), (156, 39, 176),
    (0, 188, 212), (233, 30, 99), (139, 195, 74), (121, 85, 72), (96, 125, 139),
]
_NO_DATA_COLOR = (32, 32, 32)


def _build_arg_parser():
    p = argparse.ArgumentParser(
        description='複数露光のdepth(+IR)フレームを画素ごとに合成するオフラインツール')
    p.add_argument('session_dir', type=str, help='param_sweep.pyの出力セッションディレクトリ')
    p.add_argument('--param', type=str, default='tof_exposure', metavar='NAME',
                    help='合成対象の露光パラメータ名（既定: tof_exposure）')
    p.add_argument('--values', type=str, required=True, metavar='SPEC',
                    help='合成に使う値。"1000,3000,5000" または "1000:8000:1000"（start:stop:step）')
    p.add_argument('--mode', choices=['first_valid', 'ir_aware'], default='first_valid',
                    help='合成方式（既定: first_valid）。ir_awareはIRフレームの飽和判定を使う')
    p.add_argument('--ir-saturation', type=int, default=250, metavar='N',
                    help='ir_awareモードでの飽和とみなすIR輝度のしきい値（0-255、既定: 250）')
    p.add_argument('--depth-alpha', type=float, default=0.4, metavar='A',
                    help='depth_colormap生成時のスケール（既定: 0.4。make_depth_colormap参照）')
    p.add_argument('--tag', type=str, default=None, metavar='NAME',
                    help='出力フォルダ名 hdr_compose_<tag> の<tag>部分（既定: 実行時刻）')
    return p


def _source_color(idx):
    if idx < len(_PALETTE):
        return _PALETTE[idx]
    # パレットを使い切ったら色相を均等分割して生成する
    hue = int(180 * idx / max(idx + 1, 1))
    bgr = cv2.cvtColor(np.uint8([[[hue, 200, 230]]]), cv2.COLOR_HSV2BGR)[0][0]
    return tuple(int(c) for c in bgr)


def _load_exposure_frames(session_dir, param, values, need_ir):
    """昇順に並べた値ごとに (value, depth, ir_or_None) のリストを返す。
    見つからない/読み込めない値は警告して除外する。
    """
    frames = []
    for v in sorted(values, key=float):
        casted = cast_value(param, v)
        dirname = combo_dirname({param: casted})
        vdir = session_dir / dirname
        depth_path = find_result_file(vdir, dirname, 'depth', 'png')
        if depth_path is None:
            print(f"警告: {combo_label({param: casted})} のdepthが見つかりません（{vdir}）。この値をスキップします")
            continue
        depth = cv2.imread(str(depth_path), cv2.IMREAD_UNCHANGED)
        if depth is None or depth.dtype != np.uint16:
            print(f"警告: {depth_path} の読み込みに失敗、またはuint16ではありません。スキップします")
            continue
        ir = None
        if need_ir:
            ir_path = find_result_file(vdir, dirname, 'ir', 'png')
            if ir_path is None:
                print(f"警告: {combo_label({param: casted})} のirが見つかりません（{vdir}）。"
                      "ir_awareモードではこの値は飽和判定なし（=常に有効）として扱います")
            else:
                ir = cv2.imread(str(ir_path), cv2.IMREAD_GRAYSCALE)
        frames.append({'value': casted, 'depth': depth, 'ir': ir, 'dirname': dirname})
    return frames


def compose(frames, mode, ir_saturation):
    """frames（昇順）から合成depth・source_map(uint8, index or 255=no data)を作る。"""
    shape = frames[0]['depth'].shape
    for f in frames:
        if f['depth'].shape != shape:
            raise ValueError(f"depth画像のサイズが揃っていません: {shape} vs {f['depth'].shape}")

    composed = np.zeros(shape, dtype=np.uint16)
    source_map = np.full(shape, 255, dtype=np.uint8)
    contrib = [0] * len(frames)

    def _fill(condition_fn):
        for idx, f in enumerate(frames):
            hole = source_map == 255
            if not hole.any():
                break
            ok = hole & condition_fn(f)
            composed[ok] = f['depth'][ok]
            source_map[ok] = idx
            contrib[idx] += int(ok.sum())

    def _not_saturated(f):
        if f['ir'] is None:
            return np.ones(shape, dtype=bool)
        return f['ir'] < ir_saturation

    if mode == 'ir_aware':
        _fill(lambda f: (f['depth'] > 0) & _not_saturated(f))
        # フォールバック: IR飽和でも他に候補が無い画素は、無効なままより埋める
        _fill(lambda f: f['depth'] > 0)
    else:
        _fill(lambda f: f['depth'] > 0)

    return composed, source_map, contrib


def _save_source_map_image(source_map, out_path, n_frames):
    vis = np.full((*source_map.shape, 3), _NO_DATA_COLOR, dtype=np.uint8)
    for idx in range(n_frames):
        vis[source_map == idx] = _source_color(idx)
    cv2.imwrite(str(out_path), vis)


def main():
    parser = _build_arg_parser()
    args = parser.parse_args()

    session_dir = Path(args.session_dir).expanduser()
    if not session_dir.is_dir():
        parser.error(f"セッションディレクトリが見つかりません: {session_dir}")

    values = parse_values(args.values)
    need_ir = args.mode == 'ir_aware'

    frames = _load_exposure_frames(session_dir, args.param, values, need_ir)
    if len(frames) < 2:
        parser.error(f"合成には2つ以上の有効な露光フレームが必要です（見つかったのは{len(frames)}個）")

    print(f"合成対象: {args.param} = {[f['value'] for f in frames]}（{len(frames)}枚、昇順）")
    print(f"モード: {args.mode}" + (f"  IR飽和しきい値: {args.ir_saturation}" if need_ir else ""))

    composed, source_map, contrib = compose(frames, args.mode, args.ir_saturation)

    tag = args.tag or datetime.now().strftime('%H%M%S')
    out_dir = session_dir / f'hdr_compose_{tag}'
    out_dir.mkdir(parents=True, exist_ok=True)

    cv2.imwrite(str(out_dir / 'composed_depth.png'), composed)
    composed_cm = make_depth_colormap(composed, args.depth_alpha)
    cv2.imwrite(str(out_dir / 'composed_depth_colormap.png'), composed_cm)
    _save_source_map_image(source_map, out_dir / 'source_map.png', len(frames))

    total = composed.size
    n_holes = int((source_map == 255).sum())
    print(f"\n採用画素数の内訳（総画素数 {total}）:")
    for f, n in zip(frames, contrib):
        print(f"  {args.param}={f['value']}: {n}画素 ({100 * n / total:.1f}%)")
    print(f"  無効（どの露光にも有効なdepthが無かった）: {n_holes}画素 ({100 * n_holes / total:.1f}%)")

    tiles = [(str(session_dir / f['dirname'] / result_filename(f['dirname'], 'depth_colormap', 'png')),
              f"{args.param}={f['value']}")
             for f in frames]
    tiles.append((composed_cm, 'composed'))
    comparison_path = out_dir / 'comparison.png'
    if build_montage(tiles, comparison_path, title=f'HDR compose ({args.mode}) - {args.param}'):
        print(f"比較画像: {comparison_path}")

    meta = {
        'session_dir': str(session_dir),
        'param': args.param,
        'values': [f['value'] for f in frames],
        'mode': args.mode,
        'ir_saturation': args.ir_saturation if need_ir else None,
        'depth_alpha': args.depth_alpha,
        'contrib_pixels': {str(f['value']): n for f, n in zip(frames, contrib)},
        'hole_pixels': n_holes,
        'total_pixels': total,
        'generated_at': datetime.now().isoformat(timespec='seconds'),
    }
    with open(out_dir / 'metadata.json', 'w') as fp:
        json.dump(meta, fp, indent=2, ensure_ascii=False)

    print(f"\n完了 → {out_dir}")


if __name__ == '__main__':
    main()
