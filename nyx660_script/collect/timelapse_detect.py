import sys
import os
import gc
import csv
import time
from pathlib import Path
from datetime import datetime
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils import load_config, build_parser, apply_args, init_sdk, open_camera, close_camera
from utils import extract_color, extract_depth, extract_ir, make_depth_colormap, get_bbox_depth

import cv2
import numpy as np
from ctypes import c_uint16

WARMUP_SECS = 2.0


def _build_parser():
    parser = build_parser()
    parser.add_argument('--interval', type=int, default=300,
                        help='撮影間隔（秒）デフォルト: 300（5分）')
    parser.add_argument('--duration', type=float, default=12.0,
                        help='撮影継続時間（時間）デフォルト: 12.0')
    parser.add_argument('--detect', action='store_true',
                        help='起動時にGUIでYOLOモデルを選択しBBOX付き画像も保存する')
    parser.add_argument('--relative-depth', action='store_true',
                        help='深度カラーマップをフレーム内の相対値で正規化する')
    return parser


_args = _build_parser().parse_args()
_cfg  = apply_args(load_config(), _args)
init_sdk(_cfg)

from API.ScepterDS_enums import ScFrameType

INTERVAL   = _args.interval
if INTERVAL <= 0:
    print("--interval は1以上を指定してください。")
    sys.exit(1)
TOTAL_SEC   = int(_args.duration * 3600)
TOTAL_SHOTS = TOTAL_SEC // INTERVAL

depth_alpha = None if _args.relative_depth else _cfg['camera'].get('depth_alpha', 0.4)
CONF        = float(_cfg.get('model', {}).get('confidence_threshold', 0.5))
INFER_SIZE  = (640, 480)  # depth と座標を合わせるため推論は 640×480 に統一

# --- --detect: GUIでモデルを選択 ---
model         = None
annotated_dir = None
log_path      = None

if _args.detect:
    _model_path = ''
    try:
        import tkinter as tk
        from tkinter import filedialog
        _tk = tk.Tk()
        _tk.withdraw()
        _model_path = filedialog.askopenfilename(
            title='YOLOモデルを選択（.pt）',
            initialdir=str(Path(__file__).parent.parent / 'model'),
            filetypes=[('YOLO model', '*.pt'), ('All files', '*.*')]
        )
        _tk.destroy()
    except ImportError:
        print("tkinter が必要です: sudo apt install python3-tk")
        sys.exit(1)

    if not _model_path:
        print("モデルが選択されませんでした。--detect なしで起動します。")
        _args.detect = False
    else:
        from ultralytics import YOLO
        model = YOLO(_model_path)
        print(f"モデル読み込み: {_model_path}")

# --- 出力先 ---
_timelapse_base = Path(_cfg['output']['timelapse_dir'])
_now      = datetime.now()
date_key  = _now.strftime('%Y_%m%d')
date_str  = _now.strftime('%Y-%m-%d')
time_str  = _now.strftime('%H%M%S')
_date_dir = _timelapse_base / date_key
_date_dir.mkdir(parents=True, exist_ok=True)

existing  = [d for d in _date_dir.iterdir() if d.is_dir() and d.name.startswith('timelapse')]
N         = len(existing) + 1
session_dir  = _date_dir / f"timelapse{N}_{date_str}_{time_str}_NYX660"
color_dir    = session_dir / 'color'
depth_dir    = session_dir / 'depth'
ir_dir       = session_dir / 'ir'

dirs = [color_dir, depth_dir, ir_dir]
if _args.detect:
    annotated_dir = session_dir / 'annotated'
    log_path      = session_dir / 'detection_log.csv'
    dirs.append(annotated_dir)

for d in dirs:
    d.mkdir(parents=True, exist_ok=True)

# --- カメラ初期化 ---
try:
    cam = open_camera(_cfg)
except RuntimeError as e:
    print(f"エラー: {e}")
    sys.exit(1)

# --- CSV ヘッダー（--detect 時のみ）---
if log_path:
    with open(log_path, 'w', newline='') as f:
        csv.writer(f).writerow(
            ['timestamp', 'elapsed_min', 'num_detections', 'avg_conf', 'max_conf',
             'avg_depth_m', 'classes'])

print(f"\n保存先    : {session_dir}")
print(f"撮影間隔  : {INTERVAL}秒（{INTERVAL/60:.1f}分）")
print(f"継続時間  : {_args.duration}時間（予定 {TOTAL_SHOTS} 枚）")
print(f"YOLO検出  : {'有効（信頼度閾値 ' + str(CONF) + '）' if _args.detect else '無効'}\n")

# --- ウォームアップ ---
print(f"ウォームアップ中（{WARMUP_SECS}秒）...")
_warmup_end = time.time() + WARMUP_SECS
while time.time() < _warmup_end:
    cam.scGetFrameReady(c_uint16(100))


def _capture_frame():
    """color+depth が揃った1フレームを取得して返す（最大2秒待機）"""
    cache    = {'color': None, 'depth': None, 'ir': None}
    deadline = time.time() + 2.0
    while time.time() < deadline:
        ret, frameready = cam.scGetFrameReady(c_uint16(200))
        if ret != 0:
            continue
        if frameready.color:
            ret, cf = cam.scGetFrame(ScFrameType.SC_COLOR_FRAME)
            if ret == 0:
                cache['color'] = extract_color(cf)
        if frameready.depth:
            ret, df = cam.scGetFrame(ScFrameType.SC_DEPTH_FRAME)
            if ret == 0:
                cache['depth'] = extract_depth(df)
        if frameready.ir:
            ret, irf = cam.scGetFrame(ScFrameType.SC_IR_FRAME)
            if ret == 0:
                cache['ir'] = extract_ir(irf)
        if cache['color'] is not None and cache['depth'] is not None:
            break
    return cache['color'], cache['depth'], cache['ir']


def _do_capture(shot_idx: int, start: float) -> bool:
    color, depth, ir = _capture_frame()
    if color is None or depth is None:
        print(f"[警告] フレーム欠落（shot {shot_idx}）- スキップ")
        return False

    depth_vis = make_depth_colormap(depth, depth_alpha)
    stem      = f'{shot_idx:04d}_{datetime.now().strftime("%H%M%S")}'

    cv2.imwrite(str(color_dir / f'{stem}_color.jpg'), color)
    cv2.imwrite(str(depth_dir / f'{stem}_depth.png'), depth)
    cv2.imwrite(str(depth_dir / f'{stem}_depth_colormap.jpg'), depth_vis)
    if ir is not None:
        cv2.imwrite(str(ir_dir / f'{stem}_ir.jpg'), ir)

    elapsed_min = (time.time() - start) / 60
    now_str     = datetime.now().isoformat(timespec='seconds')
    log_line    = f"[{shot_idx:3d}/{TOTAL_SHOTS}] {now_str}  {elapsed_min:.1f}min"

    if _args.detect and model is not None:
        color_infer = cv2.resize(color, INFER_SIZE)
        res         = model(color_infer, conf=CONF, show=False, save=False, verbose=False)[0]
        boxes       = res.boxes
        n           = len(boxes)
        confs       = boxes.conf.cpu().numpy() if n > 0 else np.array([])
        avg_c       = float(confs.mean()) if n > 0 else 0.0
        max_c       = float(confs.max())  if n > 0 else 0.0
        classes     = ','.join(res.names[int(c)] for c in boxes.cls.cpu().numpy()) if n > 0 else ''

        # 深度距離をアノテーション画像に追記
        annotated = res.plot()
        depths_m = []
        for box in boxes.xyxy.cpu().numpy():
            x1, y1, x2, y2 = map(int, box)
            dist = get_bbox_depth(depth, x1, y1, x2, y2)
            if dist is not None:
                depths_m.append(dist / 1000.0)
                cv2.putText(annotated, f"{dist / 1000:.2f}m",
                            (x1, max(y1 - 5, 10)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 0), 2)
        avg_depth_m = float(np.mean(depths_m)) if depths_m else 0.0

        cv2.imwrite(str(annotated_dir / f'{stem}_annotated.jpg'), annotated)

        with open(log_path, 'a', newline='') as f:
            csv.writer(f).writerow(
                [now_str, f'{elapsed_min:.1f}', n, f'{avg_c:.4f}', f'{max_c:.4f}',
                 f'{avg_depth_m:.3f}', classes])

        log_line += f"  検出:{n:2d}個  avg_conf:{avg_c:.3f}  avg_depth:{avg_depth_m:.2f}m"

    print(log_line)
    return True


try:
    start_time = time.time()
    shot_count = 0
    save_count = 0
    next_time  = start_time

    while True:
        now = time.time()

        if now - start_time >= TOTAL_SEC:
            print(f"\n設定時間 {_args.duration}時間 経過。終了します。")
            break

        if now >= next_time:
            shot_count += 1
            if _do_capture(shot_count, start_time):
                save_count += 1
            next_time = start_time + shot_count * INTERVAL
            if shot_count >= TOTAL_SHOTS:
                print(f"\n予定 {TOTAL_SHOTS} 枚完了。終了します。")
                break
        else:
            # 次の撮影まで間隔が長い場合はフレームを読み捨てる
            cam.scGetFrameReady(c_uint16(100))

finally:
    close_camera(cam)
    gc.collect()
    print(f"\n完了。試行 {shot_count} 枚 / 保存成功 {save_count} 枚。ログ: {log_path or 'なし'}")
