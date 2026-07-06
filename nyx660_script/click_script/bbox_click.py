# ==============================================================================
# Version: 1.2.0
# 
# 【操作方法】
#   左ドラッグ（空白） : 新規BBox作成
#   左ドラッグ（点上） : 四隅のドットを掴んでサイズ変更
#   左ドラッグ（面内） : 半透明部分を掴んで平行移動
#   [c]        : 前の画像からBBoxをコピー
#   [Delete]   : 選択中のBBoxを削除
#   [Arrow Keys]: 選択中のBBoxを1pxずつ微調整
#   [s]: 保存 / [z]: Undo / [d]: 次へ / [a]: 前へ / [q]: 終了
# ==============================================================================

import cv2
import os
import glob
import tkinter as tk
from tkinter import filedialog
import sys
import numpy as np

# === 設定・GUI関連 ===
def select_directory():
    root = tk.Tk()
    root.withdraw()
    dir_path = filedialog.askdirectory(title="画像フォルダ(images)を選択してください")
    root.destroy()
    if not dir_path:
        print("フォルダが選択されませんでした。終了します。")
        sys.exit()
    
    valid_extensions = ('*.jpg', '*.jpeg', '*.png', '*.JPG', '*.PNG')
    image_paths = []
    for ext in valid_extensions:
        image_paths.extend(glob.glob(os.path.join(dir_path, ext)))
    # ファイル名順（タイムスタンプ順）にソート
    return sorted(image_paths), dir_path

def print_usage():
    print("\n" + "="*60)
    print("【操作方法】")
    print("  左ドラッグ（空白） : 新規BBox作成")
    print("  左ドラッグ（点上） : サイズ変更")
    print("  左ドラッグ（面内） : 平行移動")
    print("-" * 60)
    print("  [c]        : 前の画像からBBoxをコピー (Copy)")
    print("  [Delete]   : 選択中のBBoxを削除")
    print("  [s] : 保存 / [z] : Undo")
    print("  [d] : 次の画像へ / [a] : 前の画像へ / [q] : 終了")
    print("="*60 + "\n")

# === グローバル変数 ===
image_paths, img_dir = select_directory()
base_dir = os.path.dirname(img_dir)
label_class = 0

drawing_mode = "none" 
selected_idx = -1
drag_start = (0, 0)
resize_edge = -1 
boxes = []
mx, my = -1, -1
current_index = 0

# === 補助関数 ===
def get_dist(p1, p2):
    return ((p1[0]-p2[0])**2 + (p1[1]-p2[1])**2)**0.5

def inside_bbox(x, y, bbox):
    x1, y1, x2, y2 = bbox
    return x1 <= x <= x2 and y1 <= y <= y2

# === マウスイベントコールバック ===
def mouse_event(event, x, y, flags, param):
    global ix, iy, mx, my, drawing_mode, boxes, selected_idx, drag_start, resize_edge

    mx, my = x, y
    
    if event == cv2.EVENT_LBUTTONDOWN:
        # 1. 四隅のドット判定 (リサイズ)
        for i, box in enumerate(boxes):
            corners = [(box[0], box[1]), (box[2], box[1]), (box[0], box[3]), (box[2], box[3])]
            for j, corner in enumerate(corners):
                if get_dist((x, y), corner) < 10:
                    drawing_mode = "resizing"
                    selected_idx = i
                    resize_edge = j
                    return
        # 2. 面内判定 (移動)
        for i, box in enumerate(boxes):
            if inside_bbox(x, y, box):
                drawing_mode = "moving"
                selected_idx = i
                drag_start = (x, y)
                return
        # 3. 新規作成
        drawing_mode = "creating"
        ix, iy = x, y
        selected_idx = -1

    elif event == cv2.EVENT_MOUSEMOVE:
        if drawing_mode == "moving" and selected_idx != -1:
            dx, dy = x - drag_start[0], y - drag_start[1]
            x1, y1, x2, y2 = boxes[selected_idx]
            boxes[selected_idx] = (x1+dx, y1+dy, x2+dx, y2+dy)
            drag_start = (x, y)
        elif drawing_mode == "resizing" and selected_idx != -1:
            x1, y1, x2, y2 = boxes[selected_idx]
            if resize_edge == 0: boxes[selected_idx] = (x, y, x2, y2)
            elif resize_edge == 1: boxes[selected_idx] = (x1, y, x, y2)
            elif resize_edge == 2: boxes[selected_idx] = (x, y1, x2, y)
            elif resize_edge == 3: boxes[selected_idx] = (x1, y1, x, y)
    
    elif event == cv2.EVENT_LBUTTONUP:
        if drawing_mode == "creating":
            x1, y1, x2, y2 = min(ix, x), min(iy, y), max(ix, x), max(iy, y)
            if abs(x2 - x1) > 5 and abs(y2 - y1) > 5:
                boxes.append((x1, y1, x2, y2))
                selected_idx = len(boxes) - 1
        drawing_mode = "none"

# === 描画ロジック ===
def draw_styled_bbox(img, bbox, is_selected=False):
    x1, y1, x2, y2 = map(int, bbox)
    base_color = (255, 100, 0) if is_selected else (0, 255, 255)
    overlay = img.copy()
    cv2.rectangle(overlay, (x1, y1), (x2, y2), base_color, -1)
    cv2.addWeighted(overlay, 0.25, img, 0.75, 0, img)
    corners = [(x1, y1), (x2, y1), (x1, y2), (x2, y2)]
    for pt in corners:
        cv2.circle(img, pt, 5 if not is_selected else 7, (0,0,0), -1)
        cv2.circle(img, pt, 4 if not is_selected else 6, base_color, -1)

def save_yolo_format(image_path, bboxes):
    img = cv2.imread(image_path)
    if img is None: return
    h, w, _ = img.shape
    name = os.path.splitext(os.path.basename(image_path))[0]
    txt_path = os.path.join(base_dir, "labels", f"{name}.txt")
    os.makedirs(os.path.dirname(txt_path), exist_ok=True)
    with open(txt_path, "w") as f:
        for x1, y1, x2, y2 in bboxes:
            f.write(f"{label_class} {(x1+x2)/2/w:.6f} {(y1+y2)/2/h:.6f} {(x2-x1)/w:.6f} {(y2-y1)/h:.6f}\n")
    print(f"保存完了: {txt_path}")

def load_yolo_format(image_path):
    name = os.path.splitext(os.path.basename(image_path))[0]
    txt_path = os.path.join(base_dir, "labels", f"{name}.txt")
    if not os.path.exists(txt_path): return []
    img = cv2.imread(image_path); h, w, _ = img.shape
    loaded = []
    with open(txt_path, "r") as f:
        for line in f:
            p = line.strip().split()
            if len(p) == 5:
                _, xc, yc, bw, bh = map(float, p)
                loaded.append((int((xc-bw/2)*w), int((yc-bh/2)*h), int((xc+bw/2)*w), int((yc+bh/2)*h)))
    return loaded

def load_points(image_path):
    # ファイル名（例: 1735071234.jpg）からID部分を抽出
    basename = os.path.basename(image_path)
    # 拡張子を除去し、「image_」などの接頭辞があれば除去
    token = os.path.splitext(basename)[0].replace("image_", "")
    
    # points_1735071234.txt のような形式を探す
    p_path = os.path.join(base_dir, "points", f"points_{token}.txt")
    points = []
    if os.path.exists(p_path):
        with open(p_path, "r") as f:
            for line in f:
                try:
                    # カンマ区切りまたはスペース区切りに対応
                    line = line.replace(',', ' ')
                    x, y = map(int, line.strip().split())
                    points.append((x, y))
                except: continue
    return points

# === メインループ ===
cv2.namedWindow('Image', cv2.WINDOW_NORMAL)
cv2.resizeWindow('Image', 1280, 720)
cv2.setMouseCallback('Image', mouse_event)
print_usage()

while current_index < len(image_paths):
    img_path = image_paths[current_index]
    orig_img = cv2.imread(img_path)
    if orig_img is None: current_index += 1; continue
    
    boxes = load_yolo_format(img_path)
    points = load_points(img_path)
    selected_idx = -1 

    while True:
        display = orig_img.copy()
        
        # 1. Pointの描画 (緑色の点)
        for (px, py) in points:
            cv2.circle(display, (px, py), 5, (0, 255, 0), -1)

        # 2. 十字カーソル
        cv2.line(display, (0, my), (display.shape[1], my), (150, 150, 150), 1)
        cv2.line(display, (mx, 0), (mx, display.shape[0]), (150, 150, 150), 1)

        # 3. BBoxの描画
        for i, box in enumerate(boxes):
            draw_styled_bbox(display, box, is_selected=(i == selected_idx))
        
        # 4. 作成中プレビュー
        if drawing_mode == "creating":
            cv2.rectangle(display, (ix, iy), (mx, my), (0, 0, 255), 1)

        cv2.setWindowTitle('Image', f"[{current_index+1}/{len(image_paths)}] {os.path.basename(img_path)}")
        cv2.imshow('Image', display)
        
        key = cv2.waitKey(20)

        # [c] キー : 前の画像からBBoxをコピー
        if key & 0xFF == ord('c'):
            if current_index > 0:
                prev_boxes = load_yolo_format(image_paths[current_index - 1])
                if prev_boxes:
                    boxes = prev_boxes.copy()
                    selected_idx = -1
                    print(f"前の画像からBBoxを複製しました")
                else:
                    print("⚠️ 前の画像にデータがありません")

        # [Delete] または [Backspace] : 削除
        if key == 255 or key == 0xFFFF or key == 8:
            if selected_idx != -1:
                del boxes[selected_idx]
                selected_idx = -1

        # 十字キーによる移動
        if selected_idx != -1:
            x1, y1, x2, y2 = boxes[selected_idx]
            if key == 81: boxes[selected_idx] = (x1-1, y1, x2-1, y2)
            elif key == 82: boxes[selected_idx] = (x1, y1-1, x2, y2-1)
            elif key == 83: boxes[selected_idx] = (x1+1, y1, x2+1, y2)
            elif key == 84: boxes[selected_idx] = (x1, y1+1, x2, y2+1)

        key = key & 0xFF
        if key == ord('s'): save_yolo_format(img_path, boxes)
        elif key == ord('z') and boxes: boxes.pop(); selected_idx = -1
        elif key == ord('d'): # Next
            save_yolo_format(img_path, boxes)
            current_index += 1
            break
        elif key == ord('a') and current_index > 0: # Back
            current_index -= 1
            break
        elif key == ord('q'): sys.exit()

cv2.destroyAllWindows()