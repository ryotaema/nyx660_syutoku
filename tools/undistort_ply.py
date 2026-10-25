#!/usr/bin/env python3
"""点群 PLY の座標を，ToF レンズの歪みを補正した位置に直すツール。

カメラの SDK（scConvertDepthFrameToPointCloudVector）が作る点群は ToF レンズの歪みを補正しておらず，
画面の端の遠い点ほど横にずれる（補正した位置との差は中央値 約2cm，最大 約20cm。2026-10-25 実機確認，
../log_nyx/2026-10-25.md）。撮影スクリプトが保存する PLY は SDK の値のままなので，
形の正確さが必要なときはこのツールで直す。深度（カメラからの距離）は変えず，横方向の位置だけを直す。
色（red/green/blue）はそのまま残す。

既定は dry-run で，実際に書き込むには --apply を付ける。
補正前のファイルは <名前>.ply.raw_bak として残す（--no-backup で残さない）。

使い方:
    # 何が補正されるか確認（dry-run）
    python3 tools/undistort_ply.py data

    # 実行
    python3 tools/undistort_ply.py data --apply

判定:
    - SDK の値のままの点は，ToF の内部パラメータで投影すると画素にぴったり戻る。そうでなければ補正済みとしてスキップ
      （何度実行しても二重に補正されない）
    - セッションの intrinsics.json が必要
    - mm 単位の旧形式は先に convert_ply_to_m.py で変換する
    - マージ結果（*merged_pc.ply）は補正しない。補正後に point_merge.py をやり直すこと
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'nyx660_script'))
from convert_ply_to_m import find_metadata, load_json, _MERGED
from colorize_ply import find_intrinsics, _PLY_TO_SDK
from utils import read_ply, write_ply, undistort_points, is_sdk_raw_points


def plan(ply):
    """(intrinsics.json のパス | None, 理由)"""
    if ply.name.endswith(_MERGED):
        return None, 'マージ結果（補正した後に point_merge.py をやり直してください）'
    data = read_ply(ply)
    if data is None:
        return None, '対象外の形式'
    pts, _ = data
    if len(pts) == 0:
        return None, '点が0個'
    if float(np.median(pts[:, 2])) > 0:
        return None, 'mm 単位の旧形式（先に tools/convert_ply_to_m.py で変換してください）'
    intr_path = find_intrinsics(ply)
    if intr_path is None:
        return None, 'intrinsics.json が見つかりません'
    with open(intr_path) as f:
        tof = json.load(f)['tof']
    if not is_sdk_raw_points(pts * _PLY_TO_SDK, tof):
        return None, '補正済み'
    return intr_path, '歪み未補正'


def main():
    parser = argparse.ArgumentParser(
        description='点群 PLY の座標を ToF レンズの歪みを補正した位置に直す',
        epilog='既定は dry-run。実際に書き込むには --apply を付けること。')
    parser.add_argument('path', help='対象（データルート / 日付ディレクトリ / セッションディレクトリ / PLY ファイル）')
    parser.add_argument('--apply', action='store_true', help='実際に書き換える（省略時は表示のみ）')
    parser.add_argument('--no-backup', action='store_true', help='補正前のファイル（.ply.raw_bak）を残さない')
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
        intr_path, reason = plan(ply)
        (todo if intr_path else skipped).append((ply, intr_path, reason))

    for ply, _, reason in skipped:
        print(f"[スキップ] {reason}: {ply}")
    if skipped:
        print()
    for ply, _, reason in todo:
        print(f"[補正] {reason}: {ply}")
    print(f"\n補正対象: {len(todo)} ファイル / スキップ: {len(skipped)} ファイル")

    if not args.apply:
        if todo:
            print("dry-run です。実際に補正するには --apply を付けてください。")
        return

    metas = set()
    for ply, intr_path, _ in todo:
        pts, rgb = read_ply(ply)
        with open(intr_path) as f:
            tof = json.load(f)['tof']
        fixed = undistort_points(pts * _PLY_TO_SDK, tof) / _PLY_TO_SDK
        if not args.no_backup:
            shutil.copy2(ply, ply.with_name(ply.name + '.raw_bak'))
        write_ply(ply, fixed, rgb)
        meta_path = find_metadata(ply)
        if meta_path:
            metas.add(meta_path)

    for meta_path in sorted(metas):
        meta = load_json(meta_path)
        if meta is None or not isinstance(meta.get('ply_format'), dict):
            continue
        meta['ply_format']['undistorted'] = True
        with open(meta_path, 'w') as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)

    print(f"\n完了: {len(todo)} ファイルを補正しました"
          + ("" if args.no_backup else "（補正前のファイルは .ply.raw_bak として残しています）"))
    if any(p.name.endswith(_MERGED) for p, _, _ in skipped):
        print("マージ結果にも反映するには point_merge.py をやり直してください。")


if __name__ == '__main__':
    main()
