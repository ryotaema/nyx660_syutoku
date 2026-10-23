#!/usr/bin/env python3
"""取得済みの点群 PLY を、現在の形式（m・RealSense Viewer 座標系）に変換するツール。

2026-10 より前の nyx の PLY は SDK の値そのまま（mm・OpenCV 系: x 右・y 下・z 前方）で
保存されていた。現在は real_syutoku と同じ形式（m・x 右・y 上・カメラ前方が -z）で保存して
いるので、古いデータを混ぜて使う前にこのツールで変換する。
変換は (x, y, z)[mm] → (x, -y, -z) / 1000 [m]。

既定は dry-run で、実際に書き込むには --apply を付ける。
変換前のファイルは <名前>.ply.mm_bak として残す（--no-backup で残さない）。

使い方:
    # 何が変換されるか確認（dry-run）
    python3 tools/convert_ply_to_m.py data/pointcloud

    # 実行
    python3 tools/convert_ply_to_m.py data/pointcloud --apply

    # セッションを1つだけ
    python3 tools/convert_ply_to_m.py data/pointcloud/260707/nyx_260707_101741 --apply

判定:
    - metadata.json の ply_format.unit が 'm' のセッションは変換済みとしてスキップ
    - それ以外は点の z の中央値で判定（> 20 なら mm の旧形式とみなす。
      負なら変換済み、0〜20 は判別できないのでスキップして報告）
    - マージ結果（*merged_pc.ply など）は変換しない。変換後に point_merge.py をやり直すこと
"""

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'nyx660_script'))
from utils import PLY_FORMAT

_MM_TO_M = np.array([0.001, -0.001, -0.001], dtype=np.float32)
_MERGED = ('merged_pc.ply', 'merged_pointcloud.ply')
_HEADER_XYZ = ['property float x', 'property float y', 'property float z']


def read_xyz_ply(path):
    """旧 save_ply 形式（binary little endian・float x/y/z のみ）を読む。対象外なら None。"""
    with open(path, 'rb') as f:
        data = f.read()
    end = data.find(b'end_header\n')
    if end < 0:
        return None
    header = data[:end].decode('ascii', errors='replace').splitlines()
    if 'format binary_little_endian 1.0' not in header:
        return None
    props = [l for l in header if l.startswith('property')]
    vert = [l for l in header if l.startswith('element vertex')]
    if props != _HEADER_XYZ or len(vert) != 1 or any(l.startswith('element face') for l in header):
        return None
    n = int(vert[0].split()[-1])
    body = data[end + len('end_header\n'):]
    return np.frombuffer(body, dtype='<f4', count=n * 3).reshape(-1, 3)


def write_xyz_ply(path, pts):
    header = ("ply\nformat binary_little_endian 1.0\n"
              f"element vertex {len(pts)}\n"
              "property float x\nproperty float y\nproperty float z\n"
              "end_header\n")
    with open(path, 'wb') as f:
        f.write(header.encode('ascii'))
        f.write(pts.astype('<f4').tobytes())


def find_metadata(ply):
    """PLY と同じフォルダ、なければ親フォルダ（param_tune の構成）の metadata.json。"""
    for d in (ply.parent, ply.parent.parent):
        if (d / 'metadata.json').exists():
            return d / 'metadata.json'
    return None


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def classify(ply):
    """('convert' | 'skip', 理由)"""
    if ply.name.endswith(_MERGED):
        return 'skip', 'マージ結果（変換後に point_merge.py をやり直してください）'
    meta_path = find_metadata(ply)
    meta = load_json(meta_path) if meta_path else None
    if meta and (meta.get('ply_format') or {}).get('unit') == 'm':
        return 'skip', '変換済み（metadata.json の ply_format）'
    pts = read_xyz_ply(ply)
    if pts is None:
        return 'skip', '対象外の PLY 形式（xyz のみのバイナリ PLY ではない）'
    if len(pts) == 0:
        return 'skip', '点が0個'
    z = float(np.median(pts[:, 2]))
    if z > 20:
        return 'convert', f'mm・OpenCV 系（z 中央値 {z:.0f}）'
    if z < 0:
        return 'skip', f'変換済みとみられる（z 中央値 {z:.3f}）'
    return 'skip', f'単位を判別できません（z 中央値 {z:.3f}）。手動で確認してください'


def main():
    parser = argparse.ArgumentParser(
        description='旧形式（mm・OpenCV 系）の点群 PLY を m・RealSense Viewer 座標系に変換する',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='既定は dry-run。実際に書き込むには --apply を付けること。',
    )
    parser.add_argument('path', help='変換元（データルート / 日付ディレクトリ / セッションディレクトリ / PLY ファイル）')
    parser.add_argument('--apply', action='store_true', help='実際に書き換える（省略時は表示のみ）')
    parser.add_argument('--no-backup', action='store_true',
                        help='変換前のファイル（.ply.mm_bak）を残さない')
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
        action, reason = classify(ply)
        (todo if action == 'convert' else skipped).append((ply, reason))

    for ply, reason in skipped:
        print(f"[スキップ] {reason}: {ply}")
    if skipped:
        print()
    for ply, reason in todo:
        print(f"[変換] {reason}: {ply}")
    print(f"\n変換対象: {len(todo)} ファイル / スキップ: {len(skipped)} ファイル")

    if not args.apply:
        if todo:
            print("dry-run です。実際に変換するには --apply を付けてください。")
        return

    metas = set()
    for ply, _ in todo:
        pts = read_xyz_ply(ply)
        if not args.no_backup:
            shutil.copy2(ply, ply.with_name(ply.name + '.mm_bak'))
        write_xyz_ply(ply, pts * _MM_TO_M)
        meta_path = find_metadata(ply)
        if meta_path:
            metas.add(meta_path)

    # 変換したセッションの metadata.json に形式を記録する（次回以降はスキップされる）
    now = datetime.now().isoformat(timespec='seconds')
    for meta_path in sorted(metas):
        meta = load_json(meta_path)
        if meta is None:
            print(f"[警告] metadata.json を読めないため形式を記録できません: {meta_path}")
            continue
        meta['ply_format'] = PLY_FORMAT
        meta['ply_converted'] = {'from': {'unit': 'mm', 'axes': 'opencv'}, 'at': now,
                                 'tool': 'tools/convert_ply_to_m.py'}
        with open(meta_path, 'w') as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)

    print(f"\n完了: {len(todo)} ファイルを変換しました"
          + ("" if args.no_backup else "（変換前のファイルは .ply.mm_bak として残しています）"))
    if metas:
        print("このセッションをマージ済みの場合は point_merge.py をやり直してください。")


if __name__ == '__main__':
    main()
