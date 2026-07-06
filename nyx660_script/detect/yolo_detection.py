import sys
import os
import gc
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils import load_config, build_parser, apply_args, init_sdk, open_camera, close_camera
from utils import extract_color, extract_depth, get_bbox_depth

import cv2
from ctypes import c_uint16

_args = build_parser().parse_args()
_cfg  = apply_args(load_config(), _args)
init_sdk(_cfg)

from API.ScepterDS_enums import ScFrameType

_root      = Path(__file__).parent.parent
_model_cfg = _cfg.get('model', {})
MODEL_PATH = str((_root / _model_cfg.get('yolo_path', 'model/best.pt')).resolve())
CONF       = float(_model_cfg.get('confidence_threshold', 0.5))

# NYX660 depth は 640×480 なので推論もこのサイズに統一
# → bbox 座標が depth 座標系と一致し、距離ルックアップが直接できる
INFER_SIZE = (640, 480)

if not os.path.exists(MODEL_PATH):
    print(f"YOLOモデルが見つかりません: {MODEL_PATH}")
    print("config.yaml の model.yolo_path を確認してください。")
    sys.exit(1)

from ultralytics import YOLO
model = YOLO(MODEL_PATH)
print(f"モデル読み込み: {MODEL_PATH}  推論サイズ: {INFER_SIZE}")

try:
    cam = open_camera(_cfg)
except RuntimeError as e:
    print(f"エラー: {e}")
    sys.exit(1)

_cache = {'color': None, 'depth': None}

try:
    print("[q] で終了")
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
        if color is None:
            continue

        # 640×480 に縮小して推論（depth と座標系が一致）
        color_infer = cv2.resize(color, INFER_SIZE)
        results     = model(color_infer, conf=CONF, show=False, save=False, verbose=False)
        annotated   = results[0].plot()

        # 深度距離をBBoxに追記
        if depth is not None:
            for box in results[0].boxes.xyxy.cpu().numpy():
                x1, y1, x2, y2 = map(int, box)
                dist = get_bbox_depth(depth, x1, y1, x2, y2)
                if dist is not None:
                    cv2.putText(annotated, f"{dist / 1000:.2f}m",
                                (x1, max(y1 - 5, 10)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 0), 2)

        cv2.imshow('NYX660 YOLO', annotated)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

finally:
    close_camera(cam)
    cv2.destroyAllWindows()
    gc.collect()
