import sys
import os
import gc
from pathlib import Path
from datetime import datetime
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils import load_config, init_sdk, open_camera, close_camera, extract_color

import cv2
import numpy as np
from ctypes import c_uint16

_cfg = load_config()
init_sdk(_cfg)

from API.ScepterDS_enums import ScFrameType

save_dir = os.path.join(_cfg['output']['images_dir'], 'click_test_data')
os.makedirs(save_dir, exist_ok=True)

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
            ts         = datetime.now().strftime("%Y%m%d_%H%M%S")
            image_path = os.path.join(save_dir, f"image_{ts}.jpg")
            txt_path   = os.path.join(save_dir, f"points_{ts}.txt")

            cv2.imwrite(image_path, _last_color)
            with open(txt_path, 'w') as f:
                for pt in click_points:
                    f.write(f"{pt[0]},{pt[1]}\n")

            print(f"保存しました: {image_path}, {txt_path}")
            click_points.clear()

        elif key == ord('q'):
            break

finally:
    close_camera(cam)
    cv2.destroyAllWindows()
    gc.collect()
