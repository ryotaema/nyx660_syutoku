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

path_color    = os.path.join(base_path, "color")
path_depth    = os.path.join(base_path, "depth")
path_depth_cm = os.path.join(base_path, "depth_colormap")
path_ir       = os.path.join(base_path, "ir")
for p in [path_color, path_depth, path_depth_cm, path_ir]:
    os.makedirs(p, exist_ok=True)

print(f"保存先: {base_path}")

# --- カメラ初期化 ---
try:
    cam = open_camera(_cfg)
except RuntimeError as e:
    print(f"エラー: {e}")
    sys.exit(1)

i = 0

# color/depth は別タイミングで届く場合があるためキャッシュして使う
_cache = {'color': None, 'depth': None, 'ir': None}


def _get_frames():
    ret, frameready = cam.scGetFrameReady(c_uint16(1200))
    if ret != 0:
        return None

    if frameready.color:
        ret, cf = cam.scGetFrame(ScFrameType.SC_COLOR_FRAME)
        if ret == 0:
            _cache['color'] = extract_color(cf)
    if frameready.depth:
        ret, df = cam.scGetFrame(ScFrameType.SC_DEPTH_FRAME)
        if ret == 0:
            _cache['depth'] = extract_depth(df)
    if frameready.ir:
        ret, irf = cam.scGetFrame(ScFrameType.SC_IR_FRAME)
        if ret == 0:
            _cache['ir'] = extract_ir(irf)

    color = _cache['color']
    depth = _cache['depth']
    ir    = _cache['ir']
    if color is None or depth is None:
        return None
    return color, depth, ir


def _make_preview(color, depth_cm, ir):
    color_sm = cv2.resize(color, (depth_cm.shape[1], depth_cm.shape[0]))
    top = np.hstack([color_sm, depth_cm])
    if ir is not None:
        ir_color = cv2.applyColorMap(ir, cv2.COLORMAP_JET)
        bot = np.hstack([ir_color, np.zeros_like(depth_cm)])
        return np.vstack([top, bot])
    return top


try:
    print("\n[Enter] で保存開始  [q] で終了")

    is_running = True
    while True:
        result = _get_frames()
        if result is None:
            continue
        color, depth, ir = result
        depth_cm = make_depth_colormap(depth, _depth_alpha)
        cv2.imshow('NYX660', _make_preview(color, depth_cm, ir))
        key = cv2.waitKey(1) & 0xFF
        if key == 13:
            print("保存を開始します... ([q] で停止)")
            break
        elif key == ord('q'):
            is_running = False
            break

    while is_running:
        result = _get_frames()
        if result is None:
            continue
        color, depth, ir = result
        depth_cm = make_depth_colormap(depth, _depth_alpha)
        cv2.imshow('NYX660', _make_preview(color, depth_cm, ir))

        cv2.imwrite(os.path.join(path_color,    f"{i}_color.jpg"),          color)
        cv2.imwrite(os.path.join(path_depth,    f"{i}_depth.png"),          depth)
        cv2.imwrite(os.path.join(path_depth_cm, f"{i}_depth_colormap.jpg"), depth_cm)
        if ir is not None:
            cv2.imwrite(os.path.join(path_ir, f"{i}_ir.jpg"), ir)

        i += 1
        print(f"\rsaved: {i} frames", end="", flush=True)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            print("\n保存を停止します。")
            break

finally:
    print(f"\n合計 {i} フレーム保存 → {base_path}")
    close_camera(cam)
    cv2.destroyAllWindows()
    gc.collect()
