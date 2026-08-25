#!/usr/bin/env python3
"""param_sweep.py で撮影したパラメータ振りセッションに対して、正解（ground truth）
BBoxとのIoUマッチングに基づく本当の検出率（recall）・precisionを算出する。

detect_eval.py との違い:
    detect_eval.py はYOLOの生の検出数・信頼度だけを見ており、対象物の正解数は
    分からない（同スクリプトの冒頭コメント参照）。誤検出（背景の葉を誤検出等）が
    あっても「検出数が多い」と評価してしまう可能性がある。
    本スクリプトは「カメラ固定・シーン静止のまま露光/ゲインだけを振る」という
    param_sweep.py の前提を利用し、セッション中の1枚（基準画像）にだけ人手で
    正解BBoxを描けば、その正解を全組み合わせ共通のground truthとして使い回せる、
    という考え方に基づく。これにより真の検出率（recall = TP/正解数）・
    precision（FP込みの精度）を組み合わせごとに比較できる。

前提・注意:
    - 撮影中に対象物やカメラが動いていないこと（param_sweep.py の通常運用どおり）。
      風で葉が揺れる等わずかなズレは --iou で許容範囲を調整できる（既定0.5）。
    - 正解BBoxは1セッションにつき1回描けばよい（全組み合わせで座標を使い回す）。
      別セッション（別日・別シーン）では improve init からやり直すこと。

使い方:
    # 1) 基準画像とlabelImg用ファイル一式を用意する
    python3 tools/param_tune/gt_eval.py init data/param_tune/260819/nyx_..._color_exposure+color_gain

    # 2) labelImgで基準画像にBBoxを描く（フォーマットをYOLOに切り替えて保存）
    labelImg <session_dir>/ground_truth <session_dir>/ground_truth/classes.txt <session_dir>/ground_truth

    # 3) 全組み合わせに対してYOLO推論→正解とマッチングし、検出率を集計する
    python3 tools/param_tune/gt_eval.py eval data/param_tune/260819/nyx_..._color_exposure+color_gain

出力（セッションディレクトリ直下）:
    ground_truth/reference.png       # 正解付け用の基準画像
    ground_truth/classes.txt         # labelImg用クラス一覧（モデルのnamesから生成）
    ground_truth/reference.txt       # labelImgで保存した正解BBox（YOLO形式。手動作成）
    ground_truth/reference_info.json # 基準画像の出所（どの組み合わせ由来か）
    <組み合わせ>/<組み合わせ>_gtmatch.jpg  # 正解(黄線)・TP(緑)・FP(赤)を描画した画像
    comparison_gtmatch.png           # 全組み合わせの比較画像（recall/precisionラベル付き）
    detection_rate_summary.csv       # 組み合わせごとのTP/FP/FN・recall・precision・f1
    detection_rate_summary.png       # recall/precisionのグラフ（detect_eval.pyのグラフ様式に準拠）
"""

import sys
import csv
import json
import shutil
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / 'nyx660_script'))

from common import (
    find_latest_session, combo_label, result_filename, find_result_file,
    build_montage, montage_grid_for_combos, with_reference_tile,
)
from detect_eval import _load_metadata, _resolve_sweeps, _normalize_result, _is_numeric
from utils import load_config

import cv2
import numpy as np

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
except ImportError:
    plt = None

GT_DIRNAME = 'ground_truth'
REF_BASENAME = 'reference'


def _resolve_model(args, cfg):
    model_cfg = cfg.get('model', {})
    root = Path(__file__).resolve().parent.parent.parent / 'nyx660_script'
    model_path = args.model or str((root / model_cfg.get('yolo_path', 'model/best.pt')).resolve())
    conf = args.conf if args.conf is not None else float(model_cfg.get('confidence_threshold', 0.5))
    if not Path(model_path).exists():
        print(f"YOLOモデルが見つかりません: {model_path}")
        sys.exit(1)
    return model_path, conf


def _resolve_session(args, cfg):
    if args.session_dir:
        session_dir = Path(args.session_dir)
    else:
        session_dir = find_latest_session(cfg['output']['param_tune_dir'])
        if session_dir is None:
            print(f"セッションが見つかりません: {cfg['output']['param_tune_dir']}")
            sys.exit(1)
    if not session_dir.exists():
        print(f"エラー: ディレクトリが存在しません: {session_dir}")
        sys.exit(1)
    return session_dir


def _load_saved_results(session_dir):
    meta = _load_metadata(session_dir)
    sweeps = _resolve_sweeps(meta)
    names = [s['param'] for s in sweeps]
    unit_map = {s['param']: s.get('unit', '') for s in sweeps}
    combine = meta.get('combine', 'product' if len(names) > 1 else None)
    results = [_normalize_result(r, names) for r in meta.get('results', [])]
    saved = [r for r in results if r.get('saved')]
    if not saved:
        print("保存済みの撮影データがありません。")
        sys.exit(1)
    return meta, names, unit_map, combine, saved


def cmd_init(args, cfg):
    session_dir = _resolve_session(args, cfg)
    meta, names, unit_map, combine, saved = _load_saved_results(session_dir)

    if args.combo:
        dirname = args.combo
        if not any(r['dirname'] == dirname for r in saved):
            print(f"警告: {dirname} はこのセッションの撮影結果に見つかりません（続行します）")
    else:
        auto_ref = meta.get('auto_reference')
        if auto_ref and auto_ref.get('saved'):
            dirname = auto_ref['dirname']
        else:
            dirname = saved[len(saved) // 2]['dirname']

    vdir = session_dir / dirname
    color_path = find_result_file(vdir, dirname, 'color', 'png')
    if color_path is None:
        print(f"エラー: {vdir} にcolor画像が見つかりません。--combo で別の組み合わせを指定してください。")
        sys.exit(1)

    gt_dir = session_dir / GT_DIRNAME
    gt_dir.mkdir(exist_ok=True)
    image_dst = gt_dir / f"{REF_BASENAME}.png"
    shutil.copy(color_path, image_dst)

    model_path, _ = _resolve_model(args, cfg)
    from ultralytics import YOLO
    names_map = YOLO(model_path).names
    classes_txt = gt_dir / 'classes.txt'
    with open(classes_txt, 'w') as f:
        for i in sorted(names_map):
            f.write(f"{names_map[i]}\n")

    with open(gt_dir / 'reference_info.json', 'w') as f:
        json.dump({'source_dirname': dirname, 'source_image': str(color_path)}, f, indent=2, ensure_ascii=False)

    print(f"基準画像を用意しました: {image_dst}")
    print(f"  出所: {dirname}（camera固定・シーン静止の前提で、他の全組み合わせのground truthとして使い回します）")
    print()
    print("次の手順で正解BBoxを描いてください:")
    print(f"  labelImg {gt_dir} {classes_txt} {gt_dir}")
    print(f"  1. labelImg起動後、左のツールバーで保存フォーマットを「YOLO」に切り替える")
    print(f"  2. 対象物（{', '.join(names_map.values())}）1つずつにBBoxを描く")
    print(f"  3. 保存 → {gt_dir / (REF_BASENAME + '.txt')} が生成される")
    print(f"完了したら次を実行してください:")
    print(f"  python3 {Path(__file__).name} eval {session_dir}")


def _load_yolo_txt(txt_path, img_w, img_h):
    boxes = []
    if not txt_path.exists():
        return boxes
    for line in open(txt_path):
        parts = line.split()
        if len(parts) < 5:
            continue
        cls = int(float(parts[0]))
        cx, cy, w, h = (float(v) for v in parts[1:5])
        x1, y1 = (cx - w / 2) * img_w, (cy - h / 2) * img_h
        x2, y2 = (cx + w / 2) * img_w, (cy + h / 2) * img_h
        boxes.append({'cls': cls, 'xyxy': (x1, y1, x2, y2)})
    return boxes


def _iou(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _match(gt_boxes, pred_boxes, iou_thr):
    """同クラス同士でIoU>=iou_thrの候補をIoU降順に貪欲マッチングする。
    戻り値: tp (gt_idx, pred_idx, iou) のリスト, fp (未マッチpred_idx) のリスト,
            fn (未マッチgt_idx) のリスト
    """
    candidates = []
    for gi, g in enumerate(gt_boxes):
        for pi, p in enumerate(pred_boxes):
            if g['cls'] != p['cls']:
                continue
            iou = _iou(g['xyxy'], p['xyxy'])
            if iou >= iou_thr:
                candidates.append((iou, gi, pi))
    candidates.sort(key=lambda t: t[0], reverse=True)

    matched_g, matched_p, tp = set(), set(), []
    for iou, gi, pi in candidates:
        if gi in matched_g or pi in matched_p:
            continue
        matched_g.add(gi)
        matched_p.add(pi)
        tp.append((gi, pi, iou))
    fn = [gi for gi in range(len(gt_boxes)) if gi not in matched_g]
    fp = [pi for pi in range(len(pred_boxes)) if pi not in matched_p]
    return tp, fp, fn


def _draw_matches(img, gt_boxes, pred_boxes, confs, tp, fp, fn):
    out = img.copy()
    matched_g = {gi for gi, _, _ in tp}
    for gi, g in enumerate(gt_boxes):
        x1, y1, x2, y2 = (int(v) for v in g['xyxy'])
        color = (0, 200, 0) if gi in matched_g else (0, 165, 255)  # 緑=検出済み正解 / 橙=未検出(FN)
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
        if gi not in matched_g:
            cv2.putText(out, 'MISS', (x1, max(0, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA)
    for pi in fp:
        x1, y1, x2, y2 = (int(v) for v in pred_boxes[pi]['xyxy'])
        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 0, 255), 2)  # 赤=誤検出(FP)
        cv2.putText(out, f'FP {confs[pi]:.2f}', (x1, max(0, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (0, 0, 255), 2, cv2.LINE_AA)
    return out


def cmd_eval(args, cfg):
    session_dir = _resolve_session(args, cfg)
    gt_dir = session_dir / GT_DIRNAME
    ref_img_path = gt_dir / f"{REF_BASENAME}.png"
    gt_txt = gt_dir / f"{REF_BASENAME}.txt"
    if not ref_img_path.exists() or not gt_txt.exists():
        print(f"正解ラベルが見つかりません: {gt_txt}")
        print(f"先に `python3 {Path(__file__).name} init {session_dir}` を実行し、")
        print(f"labelImgでBBoxを描いて保存してください。")
        sys.exit(1)

    ref_img = cv2.imread(str(ref_img_path))
    img_h, img_w = ref_img.shape[:2]
    gt_boxes = _load_yolo_txt(gt_txt, img_w, img_h)
    n_gt = len(gt_boxes)
    if n_gt == 0:
        print(f"正解BBoxが0件です: {gt_txt}")
        sys.exit(1)
    print(f"正解BBox数: {n_gt}（{gt_txt}）")

    meta, names, unit_map, combine, saved = _load_saved_results(session_dir)
    model_path, conf = _resolve_model(args, cfg)

    from ultralytics import YOLO
    print(f"モデル読み込み: {model_path}  信頼度閾値:{conf}  IoU閾値:{args.iou}")
    model = YOLO(model_path)
    print(f"セッション: {session_dir}\n")

    rows = []
    for r in saved:
        combo, dirname = r['combo'], r['dirname']
        vdir = session_dir / dirname
        color_path = find_result_file(vdir, dirname, 'color', 'png')
        if color_path is None:
            print(f"  警告: {vdir} に color 画像が見つかりません。スキップします。")
            continue

        res = model(str(color_path), conf=conf, verbose=False)[0]
        boxes = res.boxes
        pred_boxes = [{'cls': int(c), 'xyxy': tuple(float(v) for v in b)}
                      for c, b in zip(boxes.cls.cpu().numpy(), boxes.xyxy.cpu().numpy())]
        confs = boxes.conf.cpu().numpy() if len(boxes) else np.array([])

        tp, fp, fn = _match(gt_boxes, pred_boxes, args.iou)
        n_tp, n_fp, n_fn = len(tp), len(fp), len(fn)
        recall = n_tp / n_gt
        precision = n_tp / (n_tp + n_fp) if (n_tp + n_fp) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

        annotated = _draw_matches(cv2.imread(str(color_path)), gt_boxes, pred_boxes, confs, tp, fp, fn)
        cv2.imwrite(str(vdir / result_filename(dirname, 'gtmatch', 'jpg')), annotated)

        row = dict(combo)
        row.update({'tp': n_tp, 'fp': n_fp, 'fn': n_fn, 'recall': recall, 'precision': precision, 'f1': f1})
        row['_combo'] = combo
        row['_dirname'] = dirname
        rows.append(row)

        print(f"  {combo_label(combo, unit_map)}  TP={n_tp} FP={n_fp} FN={n_fn}  "
              f"recall={recall:.2f}({n_tp}/{n_gt})  precision={precision:.2f}")

    if not rows:
        print("評価対象の画像がありませんでした。")
        sys.exit(1)

    if len(names) == 1 and _is_numeric([r['_combo'][names[0]] for r in rows]):
        rows.sort(key=lambda r: r['_combo'][names[0]])

    csv_path = session_dir / 'detection_rate_summary.csv'
    fieldnames = names + ['tp', 'fp', 'fn', 'recall', 'precision', 'f1']
    with open(csv_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r[k] for k in fieldnames})
    print(f"\nCSV保存: {csv_path}")

    ranked = sorted(rows, key=lambda r: (r['recall'], r['precision']), reverse=True)
    print(f"\n--- ランキング（検出率recall優先、次いでprecision） 正解数={n_gt} ---")
    for i, r in enumerate(ranked[:5], 1):
        print(f"  {i}. {combo_label(r['_combo'], unit_map)}  "
              f"recall={r['recall']:.2f}({r['tp']}/{n_gt})  precision={r['precision']:.2f}")
    best = ranked[0]
    print(f"\n推奨: {combo_label(best['_combo'], unit_map)}"
          f"（recall={best['recall']:.2f}, precision={best['precision']:.2f}）")

    grid_shape, ordered, axis_info = montage_grid_for_combos(rows, names, combine, combo_key='_combo')
    ordered_rows = ordered if ordered is not None else rows

    row_labels = col_labels = row_axis_name = col_axis_name = None
    if axis_info:
        col_labels = [f"{v}{unit_map.get(axis_info['x_name'], '')}" for v in axis_info['x_values']]
        row_labels = [f"{v}{unit_map.get(axis_info['y_name'], '')}" for v in axis_info['y_values']]
        col_axis_name = axis_info['x_name']
        row_axis_name = axis_info['y_name']

    tiles = []
    for r in ordered_rows:
        if r is None:
            tiles.append((None, ''))
            continue
        vdir = session_dir / r['_dirname']
        img_path = vdir / result_filename(r['_dirname'], 'gtmatch', 'jpg')
        combo_text = combo_label(r['_combo'], unit_map).replace(', ', '\n')
        label = f"{combo_text}\nrecall:{r['recall']:.2f} prec:{r['precision']:.2f}"
        tiles.append((str(img_path) if img_path.exists() else None, label))
    comparison_path = session_dir / 'comparison_gtmatch.png'
    if build_montage(tiles, comparison_path,
                      title=f"GT detection rate - {', '.join(names)} (n_gt={n_gt})",
                      grid_shape=grid_shape,
                      row_labels=row_labels, col_labels=col_labels,
                      row_axis_name=row_axis_name, col_axis_name=col_axis_name):
        print(f"比較画像: {comparison_path}")

    if plt is None:
        print("\n警告: matplotlib が無いためグラフは省略します（pip install matplotlib）")
        return

    title = f"Detection rate (GT-matched) - {', '.join(names)} / {session_dir.name}"
    png_path = session_dir / 'detection_rate_summary.png'
    if len(names) == 1:
        _plot_single(rows, names[0], unit_map[names[0]], title, png_path)
    elif len(names) == 2 and combine == 'product':
        _plot_heatmap(rows, names, title, png_path)
    else:
        _plot_bar(rows, unit_map, title, png_path)
    print(f"グラフ保存: {png_path}")


def _plot_single(rows, name, unit, title, png_path):
    x = [r['_combo'][name] for r in rows]
    xticklabels = None if _is_numeric(x) else [str(v) for v in x]
    if xticklabels:
        x = list(range(len(rows)))

    fig, axes = plt.subplots(2, 1, figsize=(10, 8), sharex=True)
    fig.suptitle(title, fontsize=11)

    axes[0].plot(x, [r['recall'] for r in rows], marker='o', color='seagreen')
    axes[0].set_ylabel('recall (detection rate)')
    axes[0].set_title('Recall (TP / n_gt)')
    axes[0].grid(True, alpha=0.3)
    axes[0].set_ylim(0, 1.05)

    axes[1].plot(x, [r['precision'] for r in rows], marker='o', color='indianred')
    axes[1].set_ylabel('precision')
    axes[1].set_title('Precision (TP / (TP+FP))')
    axes[1].grid(True, alpha=0.3)
    axes[1].set_ylim(0, 1.05)
    axes[1].set_xlabel(f"{name}" + (f" ({unit})" if unit else ''))

    if xticklabels:
        axes[1].set_xticks(x)
        axes[1].set_xticklabels(xticklabels, rotation=30, ha='right')

    plt.tight_layout()
    fig.savefig(png_path, dpi=150)


def _plot_heatmap(rows, names, title, png_path):
    name_x, name_y = names
    xs = sorted({r['_combo'][name_x] for r in rows}, key=str)
    ys = sorted({r['_combo'][name_y] for r in rows}, key=str)
    xi = {v: i for i, v in enumerate(xs)}
    yi = {v: i for i, v in enumerate(ys)}

    grid_recall = np.full((len(ys), len(xs)), np.nan)
    grid_prec = np.full((len(ys), len(xs)), np.nan)
    for r in rows:
        i, j = yi[r['_combo'][name_y]], xi[r['_combo'][name_x]]
        grid_recall[i, j] = r['recall']
        grid_prec[i, j] = r['precision']

    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))
    fig.suptitle(title, fontsize=11)

    for ax, grid, cmap, sub_title in (
        (axes[0], grid_recall, 'Greens', 'Recall (detection rate)'),
        (axes[1], grid_prec, 'Reds', 'Precision'),
    ):
        im = ax.imshow(grid, cmap=cmap, aspect='auto', vmin=0, vmax=1)
        ax.set_xticks(range(len(xs)))
        ax.set_xticklabels([str(v) for v in xs], rotation=30, ha='right')
        ax.set_yticks(range(len(ys)))
        ax.set_yticklabels([str(v) for v in ys])
        ax.set_xlabel(name_x)
        ax.set_ylabel(name_y)
        ax.set_title(sub_title)
        for i in range(len(ys)):
            for j in range(len(xs)):
                if not np.isnan(grid[i, j]):
                    ax.text(j, i, f"{grid[i, j]:.2f}", ha='center', va='center', fontsize=8)
        fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)

    plt.tight_layout()
    fig.savefig(png_path, dpi=150)


def _plot_bar(rows, unit_map, title, png_path):
    x = list(range(len(rows)))
    xticklabels = [combo_label(r['_combo'], unit_map) for r in rows]

    fig, axes = plt.subplots(2, 1, figsize=(max(10, len(rows) * 0.6), 9), sharex=True)
    fig.suptitle(title, fontsize=11)

    axes[0].bar(x, [r['recall'] for r in rows], color='seagreen')
    axes[0].set_ylabel('recall')
    axes[0].set_title('Recall (detection rate)')
    axes[0].set_ylim(0, 1.05)
    axes[0].grid(True, alpha=0.3, axis='y')

    axes[1].bar(x, [r['precision'] for r in rows], color='indianred')
    axes[1].set_ylabel('precision')
    axes[1].set_title('Precision')
    axes[1].set_ylim(0, 1.05)
    axes[1].grid(True, alpha=0.3, axis='y')
    axes[1].set_xticks(x)
    axes[1].set_xticklabels(xticklabels, rotation=45, ha='right', fontsize=8)

    plt.tight_layout()
    fig.savefig(png_path, dpi=150)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='cmd', required=True)

    p_init = sub.add_parser('init', help='labelImgで正解を付けるための基準画像一式を用意する')
    p_init.add_argument('session_dir', nargs='?', default=None)
    p_init.add_argument('--combo', type=str, default=None,
                         help='基準画像に使う組み合わせのディレクトリ名（省略時はauto_reference、無ければ中央値の組み合わせ）')
    p_init.add_argument('--model', type=str, default=None, help='YOLOモデルパス（classes.txt生成用）')
    p_init.add_argument('--conf', type=float, default=None, help='未使用（evalとオプションを揃えるため）')

    p_eval = sub.add_parser('eval', help='正解BBoxとのIoUマッチングで検出率(recall)・precisionを集計する')
    p_eval.add_argument('session_dir', nargs='?', default=None)
    p_eval.add_argument('--model', type=str, default=None, help='YOLOモデルパス（省略時はconfig.yamlのmodel.yolo_path）')
    p_eval.add_argument('--conf', type=float, default=None, help='信頼度閾値（省略時はconfig.yamlのmodel.confidence_threshold）')
    p_eval.add_argument('--iou', type=float, default=0.5, help='正解とのマッチング判定に使うIoU閾値（既定0.5）')

    args = parser.parse_args()
    cfg = load_config()

    if args.cmd == 'init':
        cmd_init(args, cfg)
    elif args.cmd == 'eval':
        cmd_eval(args, cfg)


if __name__ == '__main__':
    main()
