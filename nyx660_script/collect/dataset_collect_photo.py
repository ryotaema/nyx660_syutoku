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
            shot_count += 1

            cv2.imwrite(session.path(shot_count, 'color'),            color)
            cv2.imwrite(session.path(shot_count, 'depth', ext='png'), depth)
            cv2.imwrite(session.path(shot_count, 'depth_colormap'),   depth_cm)
            if ir is not None:
                cv2.imwrite(session.path(shot_count, 'ir'), ir)

            print(f"[{shot_count}枚目保存] {session.name(shot_count, 'color')}")

        elif key == ord('q'):
            print(f"\n終了します。合計 {shot_count} 枚保存しました。")
            break

finally:
    session.write_metadata(
        camera={'model': 'NYX660',
                'resolution': [_cfg['camera'].get('color_width'), _cfg['camera'].get('color_height')],
                'fps': _cfg['camera'].get('fps'),
                'params_json': _cfg['camera'].get('params_json')},
        modalities=_mods,
        shot_count=shot_count,
    )
    close_camera(cam)
    cv2.destroyAllWindows()
    gc.collect()
