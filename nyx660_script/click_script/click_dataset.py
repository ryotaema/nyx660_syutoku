import sys
import gc
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils import load_config, build_parser, apply_args, init_sdk, open_camera, close_camera
from utils import extract_color, Session

import cv2
import numpy as np
from ctypes import c_uint16

_args = build_parser().parse_args()
_cfg  = apply_args(load_config(), _args)
init_sdk(_cfg)

from API.ScepterDS_enums import ScFrameType

# 画像とクリック座標をペアで置くためセッション直下にフラット配置
_base_dir = Path(_cfg['output']['images_dir']).parent / 'click_test_data'
session   = Session(_base_dir, tag=_args.tag)
print(f"保存先: {session.dir}")

click_points  = []
current_frame = None


def mouse_callback(event, x, y, flags, param):
    global click_points, current_frame
    if event == cv2.EVENT_LBUTTONDOWN:
        click_points.append((x, y))
        print(f"クリック: ({x}, {y})")
        cv2.circle(current_frame, (x, y), 5, (0, 255, 0), -1)


try:
    cam = open_camera(_cfg)
except RuntimeError as e:
    print(f"エラー: {e}")
    sys.exit(1)

print("マウスで検出対象をクリックしてください。[s] で保存、[q] で終了")
cv2.namedWindow('NYX660', cv2.WINDOW_AUTOSIZE)
cv2.setMouseCallback('NYX660', mouse_callback)

_last_color = None
shot_count  = 0

try:
    while True:
        ret, frameready = cam.scGetFrameReady(c_uint16(1200))
        if ret != 0:
            continue
        if frameready.color:
            ret, cf = cam.scGetFrame(ScFrameType.SC_COLOR_FRAME)
            if ret == 0:
                _last_color = extract_color(cf)

        if _last_color is None:
            continue

        current_frame = _last_color.copy()
        for pt in click_points:
            cv2.circle(current_frame, pt, 5, (0, 255, 0), -1)

        cv2.imshow('NYX660', current_frame)
        key = cv2.waitKey(1) & 0xFF

        if key == ord('s'):
            # 画像とクリック座標は同じ連番を共有する
            shot_count += 1
            image_path = session.path(shot_count, 'color', sub=False)
            txt_path   = session.path(shot_count, 'points', ext='txt', sub=False)

            cv2.imwrite(image_path, _last_color)
            with open(txt_path, 'w') as f:
                for pt in click_points:
                    f.write(f"{pt[0]},{pt[1]}\n")

            print(f"保存しました: {image_path}, {txt_path}")
            click_points.clear()

        elif key == ord('q'):
            break

finally:
    session.write_metadata(
        camera={'model': 'NYX660',
                'resolution': [_cfg['camera'].get('color_width'), _cfg['camera'].get('color_height')],
                'fps': _cfg['camera'].get('fps')},
        modalities=['color', 'points'],
        shot_count=shot_count,
    )
    close_camera(cam)
    cv2.destroyAllWindows()
    gc.collect()
