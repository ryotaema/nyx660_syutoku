"""
点群の位置合わせ・マージスクリプト

使い方:
  python3 process/point_merge.py <session_dir>
  python3 process/point_merge.py <session_dir> --sequential
  python3 process/point_merge.py <session_dir> --voxel-size 0.003 --icp-threshold 0.01
  python3 process/point_merge.py   # 最新セッションを自動選択

モード:
  デフォルト    全フレームをフレーム0に位置合わせ（カメラ固定・物体静止向け）
  --sequential  各フレームを前フレームに位置合わせ（物体回転・大きな変位向け）

依存:
  pip install open3d
"""

import copy
import sys
import json
import argparse
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from utils import load_config


def _import_open3d():
    try:
        import open3d as o3d
        return o3d
    except ImportError:
        print("open3d がインストールされていません。")
        print("  pip install open3d")
        sys.exit(1)


def preprocess(o3d, pcd, voxel_size, skip_downsample=False):
    pcd_proc = pcd if skip_downsample else pcd.voxel_down_sample(voxel_size)
    pcd_proc.estimate_normals(
        o3d.geometry.KDTreeSearchParamHybrid(radius=voxel_size * 2, max_nn=30)
    )
    return pcd_proc


def icp_register(o3d, source, target, threshold, init=None):
    if init is None:
        init = np.eye(4)
    result = o3d.pipelines.registration.registration_icp(
        source, target, threshold, init,
        o3d.pipelines.registration.TransformationEstimationPointToPlane(),
    )
    return result.transformation, result.fitness, result.inlier_rmse


def find_latest_session(output_dir):
    base     = Path(output_dir).expanduser()
    sessions = sorted(base.glob('*/pc*'))
    return sessions[-1] if sessions else None


def main():
    o3d    = _import_open3d()
    cfg    = load_config()
    pc_cfg = cfg['pointcloud']

    parser = argparse.ArgumentParser(
        description='点群の位置合わせ・マージ',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('session_dir', nargs='?', default=None,
                        help='dataset_point_collect.py の出力ディレクトリ（省略時は最新セッションを自動選択）')
    parser.add_argument('--voxel-size', type=float, default=None, dest='voxel_size',
                        metavar='F',
                        help=f'ダウンサンプリング解像度 (m)  [default: {pc_cfg["voxel_size"]}]')
    parser.add_argument('--icp-threshold', type=float, default=None, dest='icp_threshold',
                        metavar='F',
                        help=f'ICP 最大対応点距離 (m)  [default: {pc_cfg["icp_threshold"]}]')
    parser.add_argument('--sequential', action='store_true',
                        help='各フレームを前フレームへ位置合わせ（物体回転時に有効）')
    parser.add_argument('--no-downsample', action='store_true',
                        help='出力点群のダウンサンプリングをスキップ')
    parser.add_argument('--full-resolution', action='store_true', dest='full_resolution',
                        help='ICP前処理・出力ともにダウンサンプリングなし（低速・高品質）')
    args = parser.parse_args()

    voxel_size    = args.voxel_size    if args.voxel_size    is not None else pc_cfg['voxel_size']
    icp_threshold = args.icp_threshold if args.icp_threshold is not None else pc_cfg['icp_threshold']

    if args.session_dir is None:
        session_dir = find_latest_session(cfg['output']['pointcloud_dir'])
        if session_dir is None:
            print(f"セッションが見つかりません: {cfg['output']['pointcloud_dir']}")
            print("先に dataset_point_collect.py でデータを収集してください。")
            sys.exit(1)
        print(f"最新セッションを使用: {session_dir}")
    else:
        p = Path(args.session_dir)
        session_dir = p if p.is_absolute() else p.resolve()
        if not session_dir.exists():
            print(f"ディレクトリが見つかりません: {session_dir}")
            sys.exit(1)

    ply_files = sorted(f for f in session_dir.glob('*_pointcloud.ply')
                       if f.name != 'merged_pointcloud.ply')
    n = len(ply_files)
    if n == 0:
        print(f"*_pointcloud.ply が見つかりません: {session_dir}")
        sys.exit(1)

    full_resolution = args.full_resolution
    no_downsample   = args.no_downsample or full_resolution

    method_label = "sequential (frame→frame)" if args.sequential else "all→frame0"
    res_label    = "フルレゾリューション（低速）" if full_resolution else f"voxel_size={voxel_size} m"
    print(f"セッション : {session_dir}")
    print(f"フレーム数 : {n}")
    print(f"ICP 方式   : {method_label}")
    print(f"解像度     : {res_label}  /  icp_threshold={icp_threshold} m")
    print()

    print("点群を読み込み中...")
    pcds = []
    for i, f in enumerate(ply_files):
        pcd = o3d.io.read_point_cloud(str(f))
        if len(pcd.points) == 0:
            print(f"\n  警告: {f.name} は空です。スキップします。")
            continue
        pcds.append(pcd)
        print(f"\r  [{i+1}/{n}] {f.name}  ({len(pcd.points)} 点)", end="", flush=True)
    print()
    n = len(pcds)

    if n == 0:
        print("有効な点群がありませんでした。")
        sys.exit(1)

    transforms = [np.eye(4)]

    if n > 1:
        print("\n前処理中...")
        pcds_down = []
        for i, pcd in enumerate(pcds):
            pcds_down.append(preprocess(o3d, pcd, voxel_size, skip_downsample=full_resolution))
            print(f"\r  [{i+1}/{n}]", end="", flush=True)
        print()

        print("\nICP 位置合わせ中...")
        low_fitness_warn = []

        if args.sequential:
            cumulative = np.eye(4)
            for i in range(1, n):
                T, fitness, rmse = icp_register(
                    o3d, pcds_down[i], pcds_down[i - 1], icp_threshold)
                cumulative = cumulative @ T
                transforms.append(cumulative.copy())
                if fitness < 0.5:
                    low_fitness_warn.append((i, fitness))
                print(f"\r  [{i}/{n-1}]  fitness={fitness:.4f}  rmse={rmse:.5f}", end="", flush=True)
        else:
            for i in range(1, n):
                T, fitness, rmse = icp_register(
                    o3d, pcds_down[i], pcds_down[0], icp_threshold)
                transforms.append(T)
                if fitness < 0.5:
                    low_fitness_warn.append((i, fitness))
                print(f"\r  [{i}/{n-1}]  fitness={fitness:.4f}  rmse={rmse:.5f}", end="", flush=True)
        print()

        if low_fitness_warn:
            print("\n  警告: 以下のフレームは位置合わせ精度が低い可能性があります（fitness < 0.5）")
            for idx, fit in low_fitness_warn:
                print(f"    frame {idx:04d}: fitness={fit:.4f}")

    print("\n点群をマージ中...")
    merged = o3d.geometry.PointCloud()
    for pcd, T in zip(pcds, transforms):
        merged += copy.deepcopy(pcd).transform(T)

    if not no_downsample:
        before = len(merged.points)
        merged = merged.voxel_down_sample(voxel_size)
        print(f"ダウンサンプリング: {before:,} → {len(merged.points):,} 点")

    out_path = session_dir / 'merged_pointcloud.ply'
    o3d.io.write_point_cloud(str(out_path), merged)
    print(f"完了: {len(merged.points):,} 点 → {out_path}")

    result_meta = {
        'input_frames':  n,
        'method':        'sequential' if args.sequential else 'to_frame0',
        'voxel_size':    voxel_size,
        'icp_threshold': icp_threshold,
        'output_points': len(merged.points),
        'output_file':   'merged_pointcloud.ply',
        'transforms':    [T.tolist() for T in transforms],
    }
    with open(session_dir / 'merge_result.json', 'w') as f:
        json.dump(result_meta, f, indent=2)
    print(f"変換行列 → merge_result.json")


if __name__ == '__main__':
    main()
