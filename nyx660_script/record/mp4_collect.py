import sys
import os
import gc
from pathlib import Path
from datetime import datetime
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils import load_config, build_parser, apply_args, init_sdk, open_camera, close_camera
from utils import extract_color, extract_depth, make_depth_colormap

import cv2
import numpy as np
from ctypes import c_uint16

_args = build_parser().parse_args()
_cfg  = apply_args(load_config(), _args)
init_sdk(_cfg)

from API.ScepterDS_enums import ScFrameType

FPS         = int(_cfg['camera'].get('fps', 30))
depth_alpha = _cfg['camera'].get('depth_alpha', 0.4)
save_dir    = _cfg['output']['mp4_dir']
os.makedirs(save_dir, exist_ok=True)

timestamp  = datetime.now().strftime('%Y%m%d_%H%M%S')
color_path = os.path.join(save_dir, f'color_{timestamp}.mp4')
depth_path = os.path.join(save_dir, f'depth_{timestamp}.mp4')

try:
    cam = open_camera(_cfg)
except RuntimeError as e:
    print(f"エラー: {e}")
    sys.exit(1)

fourcc = cv2.VideoWriter_fourcc(*'mp4v')
vw_color = None
vw_depth = None

_cache = {'color': None, 'depth': None}

try:
    print(f"保存先: {save_dir}")
    print("[Enter] で録画開始  [q] で終了")

    # --- プレビューループ（録画待機）---
    while True:
        ret, frameready = cam.scGetFrameReady(c_uint16(1200))
        if ret != 0:
            continue
        if frameready.color:
            ret, cf = cam.scGetFrame(ScFrameType.SC_COLOR_FRAME)
            if ret == 0:
                _cache['color'] = extract_color(cf)
        if frameready.depth:
            ret, df = cam.scGetFrame(ScFrameType.SC_DEPTH_FRAME)
            if ret == 0:
                _cache['depth'] = extract_depth(df)

        color = _cache['color']
        depth = _cache['depth']
        if color is None or depth is None:
            continue

        depth_cm  = make_depth_colormap(depth, depth_alpha)
        color_sm  = cv2.resize(color, (depth_cm.shape[1], depth_cm.shape[0]))
        preview   = np.hstack([color_sm, depth_cm])
        cv2.putText(preview, "[Enter] Start  [q] Quit",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        cv2.imshow('NYX660 Recorder', preview)

        key = cv2.waitKey(1) & 0xFF
        if key == 13:
            break
        elif key == ord('q'):
            sys.exit(0)

    color_w  = int(_cfg['camera'].get('color_width', 640))
    color_h  = int(_cfg['camera'].get('color_height', 480))
    h, w     = 480, 640
    vw_color = cv2.VideoWriter(color_path, fourcc, FPS, (color_w, color_h))
    vw_depth = cv2.VideoWriter(depth_path, fourcc, FPS, (w, h))
    if not vw_color.isOpened() or not vw_depth.isOpened():
        print("VideoWriter の初期化に失敗しました。")
        sys.exit(1)

    frame_count = 0
    print(f"録画開始 → {color_path}")
    print("[q] で停止")

    # --- 録画ループ ---
    while True:
        ret, frameready = cam.scGetFrameReady(c_uint16(1200))
        if ret != 0:
            continue
        if frameready.color:
            ret, cf = cam.scGetFrame(ScFrameType.SC_COLOR_FRAME)
            if ret == 0:
                _cache['color'] = extract_color(cf)
        if frameready.depth:
            ret, df = cam.scGetFrame(ScFrameType.SC_DEPTH_FRAME)
            if ret == 0:
                _cache['depth'] = extract_depth(df)

        color = _cache['color']
        depth = _cache['depth']
        if color is None or depth is None:
            continue

        vw_color.write(color)
        depth_cm = make_depth_colormap(depth, depth_alpha)
        vw_depth.write(depth_cm)

        color_sm = cv2.resize(color, (depth_cm.shape[1], depth_cm.shape[0]))
        preview  = np.hstack([color_sm, depth_cm])
        cv2.putText(preview, f"REC  {frame_count} frames  [q] Stop",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        cv2.imshow('NYX660 Recorder', preview)

        frame_count += 1
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

finally:
    if vw_color:
        vw_color.release()
    if vw_depth:
        vw_depth.release()
    close_camera(cam)
    cv2.destroyAllWindows()
    gc.collect()
    print(f"\n録画完了: {frame_count if 'frame_count' in dir() else 0} フレーム")
    print(f"  color → {color_path}")
    print(f"  depth → {depth_path}")
