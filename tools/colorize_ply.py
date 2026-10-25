#!/usr/bin/env python3
"""色の無い点群 PLY に、同じショットのカラー画像の色を付けるツール。

2026-10-25 より前の nyx の PLY は座標（x, y, z）だけで、CloudCompare などでは灰色で表示される。
各点をカラーカメラに投影し（セッションの intrinsics.json の内部・外部パラメータを使う）、
その画素の色を付けて PLY を書き直す。座標は変えない。
カラーカメラから見て手前の物に隠れる点と、カラー画像の外に写る点は灰色になる。

既定は dry-run で、実際に書き込むには --apply を付ける。

使い方:
    # 何に色が付くか確認（dry-run）
    python3 tools/colorize_ply.py data

    # 実行
    python3 tools/colorize_ply.py data --apply

対象:
    - m 単位（現在の形式）で、色を持たない PLY。mm 単位の旧形式は先に convert_ply_to_m.py で変換する
    - 同じ連番のカラー画像（color_raw/*_cr.png，無ければ *_c.jpg）と intrinsics.json があるもの
    - マージ結果（*merged_pc.ply）は対象外。色を付けた後に point_merge.py をやり直す
"""

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'nyx660_script'))
from convert_ply_to_m import find_metadata, load_json, _MERGED
from utils import point_colors, write_ply, read_ply, is_sdk_raw_points

# PLY の座標（m・RealSense Viewer 座標系）→ SDK の座標（mm・x右/y下/z前）
_PLY_TO_SDK = np.array([1000., -1000., -1000.])


def find_color(ply):
    """PLY と同じ連番のカラー画像。ロスレスの color_raw を優先する。"""
    stem = ply.stem
    if not stem.endswith('_pc'):
        return None
    base = stem[:-len('_pc')]
    for d in (ply.parent, ply.parent.parent):
        for sub, name in (('color_raw', f'{base}_cr.png'), ('', f'{base}_cr.png'),
                          ('color', f'{base}_c.jpg'), ('', f'{base}_c.jpg')):
            p = d / sub / name if sub else d / name
            if p.exists():
                return p
    return None


def find_intrinsics(ply):
    for d in (ply.parent, ply.parent.parent):
        if (d / 'intrinsics.json').exists():
            return d / 'intrinsics.json'
    return None


def plan(ply):
    """(色を付けるなら (カラー画像, intrinsics.json, aligned) | None, 理由)"""
    if ply.name.endswith(_MERGED):
        return None, 'マージ結果（色を付けた後に point_merge.py をやり直してください）'
    data = read_ply(ply)
    if data is None:
        return None, '対象外の形式'
    pts, rgb = data
    if rgb is not None:
        return None, '色付き済み'
    if len(pts) == 0:
        return None, '点が0個'
    if float(np.median(pts[:, 2])) > 0:
        return None, 'mm 単位の旧形式（先に tools/convert_ply_to_m.py で変換してください）'
    color, intr = find_color(ply), find_intrinsics(ply)
    if color is None:
        return None, '同じ連番のカラー画像が見つかりません'
    if intr is None:
        return None, 'intrinsics.json が見つかりません'
    meta_path = find_metadata(ply)
    meta = load_json(meta_path) if meta_path else None
    aligned = bool(meta and meta.get('color_aligned_to_depth'))
    return (color, intr, aligned), f'{color.name}{"（depth に位置合わせ済み）" if aligned else ""}'


def main():
    parser = argparse.ArgumentParser(
        description='色の無い点群 PLY に，同じショットのカラー画像の色を付ける',
        epilog='既定は dry-run。実際に書き込むには --apply を付けること。')
    parser.add_argument('path', help='対象（データルート / 日付ディレクトリ / セッションディレクトリ / PLY ファイル）')
    parser.add_argument('--apply', action='store_true', help='実際に書き換える（省略時は表示のみ）')
    args = parser.parse_args()

    root = Path(args.path).expanduser().resolve()
    if root.is_file():
        plys = [root]
    elif root.is_dir():
        plys = sorted(root.rglob('*.ply'))
    else:
        print(f"見つかりません: {root}")
        sys.exit(1)

    todo, skipped = [], []
    for ply in plys:
        job, reason = plan(ply)
        (todo if job else skipped).append((ply, job, reason))

    for ply, _, reason in skipped:
        print(f"[スキップ] {reason}: {ply}")
    if skipped:
        print()
    for ply, _, reason in todo:
        print(f"[色付け] {reason}: {ply}")
    print(f"\n色付け対象: {len(todo)} ファイル / スキップ: {len(skipped)} ファイル")

    if not args.apply:
        if todo:
            print("dry-run です。実際に色を付けるには --apply を付けてください。")
        return

    metas = set()
    for ply, (color_path, intr_path, aligned), _ in todo:
        pts = read_ply(ply)[0].astype(np.float64)
        color = cv2.imread(str(color_path), cv2.IMREAD_COLOR)
        with open(intr_path) as f:
            intr = json.load(f)
        sdk = pts * _PLY_TO_SDK
        # tools/undistort_ply.py で補正済みの点にも正しく色を付ける
        rgb = point_colors(sdk, color, intr, aligned=aligned,
                           undistorted=not is_sdk_raw_points(sdk, intr['tof']))
        write_ply(ply, pts, rgb)
        meta_path = find_metadata(ply)
        if meta_path:
            metas.add(meta_path)

    for meta_path in sorted(metas):
        meta = load_json(meta_path)
        if meta is None or not isinstance(meta.get('ply_format'), dict):
            continue
        meta['ply_format']['color'] = True
        with open(meta_path, 'w') as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)

    print(f"\n完了: {len(todo)} ファイルに色を付けました")
    if any(p.name.endswith(_MERGED) for p, _, _ in skipped):
        print("マージ結果にも色を付けるには point_merge.py をやり直してください。")


if __name__ == '__main__':
    main()
