import sys
import os
import gc
import numpy as np
import cv2
from pathlib import Path
from datetime import datetime
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils import load_config, build_parser, apply_args, init_sdk, open_camera, close_camera
from utils import extract_depth, extract_color, extract_ir, make_depth_colormap

_args = build_parser().parse_args()
_cfg  = apply_args(load_config(), _args)
init_sdk(_cfg)

from API.ScepterDS_enums import ScFrameType
from ctypes import c_uint16

_depth_alpha = _cfg['camera'].get('depth_alpha', 0.4)

# --- 保存ディレクトリ設定 ---
save_dir_base = os.path.expanduser(_cfg['output']['images_dir'])
_now     = datetime.now()
date_key = _now.strftime('%Y_%m%d')
date_str = _now.strftime('%Y-%m-%d')
time_str = _now.strftime('%H%M%S')
save_dir_dated = os.path.join(save_dir_base, date_key)
os.makedirs(save_dir_dated, exist_ok=True)

existing = [d for d in os.scandir(save_dir_dated) if d.is_dir() and d.name.startswith('image')]
N = len(existing) + 1
base_path = os.path.join(save_dir_dated, f"image{N}_{date_str}_{time_str}_NYX660")
os.makedirs(base_path, exist_ok=True)
print(f"保存先: {base_path}")

# --- カメラ初期化 ---
try:
    cam = open_camera(_cfg)
except RuntimeError as e:
    print(f"エラー: {e}")
    sys.exit(1)

shot_count = 0

try:
    print("\n[s] で1枚保存  [q] で終了\n")

    while True:
        ret, frameready = cam.scGetFrameReady(c_uint16(1200))
        if ret != 0:
            continue

        color = depth = ir = None
        if frameready.color:
            ret, cf = cam.scGetFrame(ScFrameType.SC_COLOR_FRAME)
            if ret == 0:
                color = extract_color(cf)
        if frameready.depth:
            ret, df = cam.scGetFrame(ScFrameType.SC_DEPTH_FRAME)
            if ret == 0:
                depth = extract_depth(df)
        if frameready.ir:
            ret, irf = cam.scGetFrame(ScFrameType.SC_IR_FRAME)
            if ret == 0:
                ir = extract_ir(irf)

        if color is None or depth is None:
            continue

        depth_cm = make_depth_colormap(depth, _depth_alpha)
        color_sm = cv2.resize(color, (depth_cm.shape[1], depth_cm.shape[0]))
        top = np.hstack([color_sm, depth_cm])
        if ir is not None:
            ir_color = cv2.applyColorMap(ir, cv2.COLORMAP_JET)
            top = np.vstack([top, np.hstack([ir_color, np.zeros_like(depth_cm)])])

        cv2.putText(top, f"[s] Save  [q] Quit   Saved: {shot_count}",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        cv2.imshow('NYX660', top)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('s'):
            ts = datetime.now().strftime('%H%M%S_%f')[:-2]

            path_color    = os.path.join(base_path, "color");         os.makedirs(path_color,    exist_ok=True)
            path_depth    = os.path.join(base_path, "depth");         os.makedirs(path_depth,    exist_ok=True)
            path_depth_cm = os.path.join(base_path, "depth_colormap"); os.makedirs(path_depth_cm, exist_ok=True)

            cv2.imwrite(os.path.join(path_color,    f"{ts}_color.jpg"),          color)
            cv2.imwrite(os.path.join(path_depth,    f"{ts}_depth.png"),          depth)
            cv2.imwrite(os.path.join(path_depth_cm, f"{ts}_depth_colormap.jpg"), depth_cm)
            if ir is not None:
                path_ir = os.path.join(base_path, "ir"); os.makedirs(path_ir, exist_ok=True)
                cv2.imwrite(os.path.join(path_ir, f"{ts}_ir.jpg"), ir)

            shot_count += 1
            print(f"[{shot_count}枚目保存] {ts}")

        elif key == ord('q'):
            print(f"\n終了します。合計 {shot_count} 枚保存しました。")
            break

finally:
    close_camera(cam)
    cv2.destroyAllWindows()
    gc.collect()
