import sys
import gc
import numpy as np
import cv2
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils import load_config, build_parser, apply_args, init_sdk, open_camera, close_camera
from utils import extract_depth, extract_color, extract_ir, make_depth_colormap, Session

_args = build_parser().parse_args()
_cfg  = apply_args(load_config(), _args)
init_sdk(_cfg)

from API.ScepterDS_enums import ScFrameType
from ctypes import c_uint16

_depth_alpha = _cfg['camera'].get('depth_alpha', 0.4)

# --- 保存ディレクトリ設定 ---
_mods   = ['color', 'depth', 'depth_colormap', 'ir']
session = Session(_cfg['output']['images_dir'], tag=_args.tag, subdirs=_mods)

print(f"保存先: {session.dir}")

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

        i += 1
        cv2.imwrite(session.path(i, 'color'),                color)
        cv2.imwrite(session.path(i, 'depth', ext='png'),     depth)
        cv2.imwrite(session.path(i, 'depth_colormap'),       depth_cm)
        if ir is not None:
            cv2.imwrite(session.path(i, 'ir'), ir)

        print(f"\rsaved: {i} frames", end="", flush=True)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            print("\n保存を停止します。")
            break

finally:
    print(f"\n合計 {i} フレーム保存 → {session.dir}")
    session.write_metadata(
        camera={'model': 'NYX660',
                'resolution': [_cfg['camera'].get('color_width'), _cfg['camera'].get('color_height')],
                'fps': _cfg['camera'].get('fps'),
                'params_json': _cfg['camera'].get('params_json')},
        modalities=_mods,
        shot_count=i,
    )
    close_camera(cam)
    cv2.destroyAllWindows()
    gc.collect()
