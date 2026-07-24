import sys
import os
import gc
import json
import numpy as np
import cv2
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils import load_config, build_parser, apply_args, init_sdk, open_camera, close_camera
from utils import extract_depth, extract_color, make_depth_colormap, save_ply, Session

_parser = build_parser()
_parser.add_argument('--frames', type=int, default=None, metavar='N',
                     help='auto モードのフレーム数（config.yaml の値を上書き）')
_parser.add_argument('--mode', choices=['auto', 'manual'], default='auto',
                     help='auto: N フレーム自動取得 | manual: [s] キーで1枚ずつ取得')
_args = _parser.parse_args()
_cfg  = apply_args(load_config(), _args)
init_sdk(_cfg)

from API.ScepterDS_enums import ScFrameType, ScSensorType
from ctypes import c_uint16

mode           = _args.mode
capture_frames = _args.frames if _args.frames is not None else _cfg['pointcloud']['capture_frames']
_depth_alpha   = _cfg['camera'].get('depth_alpha', 0.4)

# --- 保存ディレクトリ設定 ---
# color/depth/ply は点群処理でまとめて扱うためセッション直下にフラットに置く
# （point_merge.py が session_dir 直下を走査する）
session  = Session(_cfg['output']['pointcloud_dir'], tag=_args.tag)
save_dir = str(session.dir)

mode_label = f"auto ({capture_frames} frames)" if mode == 'auto' else "manual"
print(f"保存先: {save_dir}")
print(f"モード: {mode_label}")

# --- カメラ初期化 ---
try:
    cam = open_camera(_cfg)
except RuntimeError as e:
    print(f"エラー: {e}")
    sys.exit(1)

# --- 内部パラメータ保存 ---
ret, tof_intr  = cam.scGetSensorIntrinsicParameters(ScSensorType.SC_TOF_SENSOR)
ret2, col_intr = cam.scGetSensorIntrinsicParameters(ScSensorType.SC_COLOR_SENSOR)
ret3, extr     = cam.scGetSensorExtrinsicParameters()

intrinsics_data = {
    'tof': {
        'fx': tof_intr.fx, 'fy': tof_intr.fy,
        'cx': tof_intr.cx, 'cy': tof_intr.cy,
        'k1': tof_intr.k1, 'k2': tof_intr.k2,
        'p1': tof_intr.p1, 'p2': tof_intr.p2,
        'k3': tof_intr.k3, 'k4': tof_intr.k4,
        'k5': tof_intr.k5, 'k6': tof_intr.k6,
    },
    'color': {
        'fx': col_intr.fx, 'fy': col_intr.fy,
        'cx': col_intr.cx, 'cy': col_intr.cy,
        'k1': col_intr.k1, 'k2': col_intr.k2,
        'p1': col_intr.p1, 'p2': col_intr.p2,
        'k3': col_intr.k3,
    },
    'extrinsics': {
        'rotation':    list(extr.rotation),
        'translation': list(extr.translation),
    },
}
with open(os.path.join(save_dir, 'intrinsics.json'), 'w') as f:
    json.dump(intrinsics_data, f, indent=2)

# --- メタデータ初期化 ---
_frames_meta = []


def save_frame(idx, cam):
    ret, frameready = cam.scGetFrameReady(c_uint16(1200))
    if ret != 0:
        return False

    color = depth = None
    if frameready.color:
        ret, cf = cam.scGetFrame(ScFrameType.SC_COLOR_FRAME)
        if ret == 0:
            color = extract_color(cf)
    if frameready.depth:
        ret, df = cam.scGetFrame(ScFrameType.SC_DEPTH_FRAME)
        if ret == 0:
            depth  = extract_depth(df)
            hw_ts  = df.hardwaretimestamp

    if color is None or depth is None:
        return False

    cv2.imwrite(session.path(idx, 'color', sub=False), color)
    cv2.imwrite(session.path(idx, 'depth', ext='png', sub=False), depth)

    ret, pointlist = cam.scConvertDepthFrameToPointCloudVector(df)
    if ret == 0:
        save_ply(session.path(idx, 'pointcloud', ext='ply', sub=False),
                 pointlist, df.width * df.height)

    _frames_meta.append({'index': idx, 'hardwaretimestamp': int(hw_ts)})
    return True


def write_metadata(actual_frames):
    session.write_metadata(
        camera={'model': 'NYX660',
                'resolution': [_cfg['camera'].get('color_width'), _cfg['camera'].get('color_height')],
                'fps': _cfg['camera'].get('fps', 30),
                'params_json': _cfg['camera'].get('params_json')},
        mode=mode,
        capture_frames=capture_frames if mode == 'auto' else None,
        actual_frames=actual_frames,
        frames=_frames_meta,
    )


frame_count = 0

try:
    if mode == 'auto':
        print("\n[Enter] で取得開始  [q] で中止")
        while True:
            ret, frameready = cam.scGetFrameReady(c_uint16(1200))
            if ret != 0:
                continue
            if frameready.color:
                ret, cf = cam.scGetFrame(ScFrameType.SC_COLOR_FRAME)
                if ret == 0:
                    preview = extract_color(cf)
                    cv2.putText(preview, "[Enter] Start  [q] Quit",
                                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                    cv2.imshow('NYX660_pointcloud', cv2.resize(preview, (800, 600)))
            key = cv2.waitKey(1) & 0xFF
            if key == 13:
                cv2.destroyAllWindows()
                break
            elif key == ord('q'):
                print("中止しました。")
                sys.exit(0)

        print(f"取得中... (0/{capture_frames})")
        while frame_count < capture_frames:
            if save_frame(frame_count + 1, cam):
                frame_count += 1
                print(f"\rsaved: {frame_count}/{capture_frames} frames", end="", flush=True)
        print()

    else:  # manual
        print("\n[s] で1フレーム取得  [q] で終了")
        while True:
            ret, frameready = cam.scGetFrameReady(c_uint16(1200))
            if ret != 0:
                continue
            if frameready.color:
                ret, cf = cam.scGetFrame(ScFrameType.SC_COLOR_FRAME)
                if ret == 0:
                    preview = extract_color(cf)
                    cv2.putText(preview, f"[s] Save ({frame_count} saved)  [q] Quit",
                                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                    cv2.imshow('NYX660_pointcloud', cv2.resize(preview, (800, 600)))
            key = cv2.waitKey(1) & 0xFF
            if key == ord('s'):
                if save_frame(frame_count + 1, cam):
                    frame_count += 1
                    print(f"saved: {frame_count} frames")
            elif key == ord('q'):
                break

finally:
    close_camera(cam)
    cv2.destroyAllWindows()
    write_metadata(frame_count)
    print(f"完了: {frame_count} フレーム保存 → {save_dir}")
    gc.collect()
