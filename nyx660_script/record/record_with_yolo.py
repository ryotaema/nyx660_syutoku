import sys
import os
import gc
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils import load_config, build_parser, apply_args, init_sdk, open_camera, close_camera
from utils import extract_color, extract_depth, get_bbox_depth, make_prefix

import cv2
import numpy as np
from ctypes import c_uint16

_args = build_parser().parse_args()
_cfg  = apply_args(load_config(), _args)
init_sdk(_cfg)

from API.ScepterDS_enums import ScFrameType

FPS        = int(_cfg['camera'].get('fps', 30))
save_dir   = _cfg['output']['mp4_dir']
os.makedirs(save_dir, exist_ok=True)

_root      = Path(__file__).parent.parent
_model_cfg = _cfg.get('model', {})
MODEL_PATH = str((_root / _model_cfg.get('yolo_path', 'model/best.pt')).resolve())
CONF       = float(_model_cfg.get('confidence_threshold', 0.5))

# depth と座標を合わせるため推論は 640×480 に統一
INFER_SIZE = (640, 480)

if not os.path.exists(MODEL_PATH):
    print(f"YOLOモデルが見つかりません: {MODEL_PATH}")
    sys.exit(1)

from ultralytics import YOLO
model = YOLO(MODEL_PATH)
print(f"モデル読み込み: {MODEL_PATH}")

# 動画はショット連番を持たないため {cam}_{YYMMDD}_{HHMMSS}_{種別}.{ext} で命名する
mp4_path = os.path.join(save_dir, f'{make_prefix()}_det.mp4')

try:
    cam = open_camera(_cfg)
except RuntimeError as e:
    print(f"エラー: {e}")
    sys.exit(1)

fourcc  = cv2.VideoWriter_fourcc(*'mp4v')
vw      = None
_cache  = {'color': None, 'depth': None}
frame_count = 0


def _infer_and_annotate(color, depth):
    """color を INFER_SIZE で推論し、depth 距離付き annotated 画像 (INFER_SIZE) を返す。"""
    color_infer = cv2.resize(color, INFER_SIZE)
    results     = model(color_infer, conf=CONF, show=False, save=False, verbose=False)
    annotated   = results[0].plot()
    if depth is not None:
        for box in results[0].boxes.xyxy.cpu().numpy():
            x1, y1, x2, y2 = map(int, box)
            dist = get_bbox_depth(depth, x1, y1, x2, y2)
            if dist is not None:
                cv2.putText(annotated, f"{dist / 1000:.2f}m",
                            (x1, max(y1 - 5, 10)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 0), 2)
    return annotated


try:
    print(f"保存先: {mp4_path}")
    print("[Enter] で録画開始  [q] で終了")

    # --- プレビューループ ---
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

        if _cache['color'] is None:
            continue

        annotated = _infer_and_annotate(_cache['color'], _cache['depth'])
        cv2.putText(annotated, "[Enter] Start  [q] Quit",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.imshow('NYX660 YOLO Recorder', annotated)

        key = cv2.waitKey(1) & 0xFF
        if key == 13:
            break
        elif key == ord('q'):
            sys.exit(0)

    # VideoWriter は INFER_SIZE で初期化（録画サイズ = 640×480）
    vw = cv2.VideoWriter(mp4_path, fourcc, FPS, INFER_SIZE)
    if not vw.isOpened():
        print("VideoWriter の初期化に失敗しました。")
        sys.exit(1)

    print(f"録画開始 [q] で停止")

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

        if _cache['color'] is None:
            continue

        annotated = _infer_and_annotate(_cache['color'], _cache['depth'])
        vw.write(annotated)
        frame_count += 1

        cv2.putText(annotated, f"REC  {frame_count} frames  [q] Stop",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
        cv2.imshow('NYX660 YOLO Recorder', annotated)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

finally:
    if vw:
        vw.release()
    close_camera(cam)
    cv2.destroyAllWindows()
    gc.collect()
    print(f"\n録画完了: {frame_count} フレーム → {mp4_path}")
