#!/usr/bin/env python3
"""param_sweep.py で撮影したパラメータ振りセッション（単一・複数パラメータ両対応）に対して
組み合わせごとにYOLO検出を実行し、検出数・信頼度を集計してどのパラメータ値が有用か
判断する材料を作る。

使い方:
    # セッションディレクトリを指定
    python3 tools/param_tune/detect_eval.py data/param_tune/260818/nyx_260818_..._tof_exposure

    # 省略時は param_tune_dir 配下の最新セッションを自動選択
    python3 tools/param_tune/detect_eval.py

    # モデル・信頼度閾値を明示指定（省略時は config.yaml の model.* を使用）
    python3 tools/param_tune/detect_eval.py <session_dir> --model model/best.pt --conf 0.4

出力（セッションディレクトリ直下）:
    <組み合わせ>/<組み合わせ>_detected.jpg  # BBox描画済み画像（旧命名のdetected.jpgにも対応）
    comparison_detected.png    # 全組み合わせのBBox描画済み画像を1枚に並べた比較画像
                                # （検出数・平均信頼度もラベルに表示。Auto参考撮影があれば
                                #   1行グリッド/自動配置の場合のみ先頭に含める）
    detection_summary.csv      # 組み合わせごとの検出数・平均/最大信頼度・検出クラス
                                # （Auto参考撮影は含めない。結果は auto_reference/detection.json）
    detection_summary.png      # パラメータ1個: 値に対する折れ線グラフ
                                # パラメータ2個(product): 値×値のヒートマップ
                                # それ以外（3個以上 or zip）: 組み合わせごとの棒グラフ

「有用」の判定基準はここでは検出数・信頼度の高さのみ（対象物の正解数は
このスクリプトからは分からないため）。撮影シーンを固定した比較用の目安として使うこと。
"""

import sys
import csv
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / 'nyx660_script'))

from common import (
    safe_dirname, find_latest_session, combo_label,
    result_filename, find_result_file, build_montage, montage_grid_for_combos, with_reference_tile,
)
from utils import load_config

import cv2
import numpy as np

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
except ImportError:
    plt = None


def _load_metadata(session_dir):
    meta_path = session_dir / 'metadata.json'
    if not meta_path.exists():
        print(f"エラー: metadata.json が見つかりません: {meta_path}")
        sys.exit(1)
    with open(meta_path) as f:
        return json.load(f)


def _resolve_sweeps(meta):
    """新形式('sweeps')・旧形式（単一パラメータの'param'）どちらのmetadata.jsonにも対応する。"""
    if 'sweeps' in meta:
        return meta['sweeps']
    if 'param' in meta:
        return [{'param': meta['param'], 'label': meta.get('param_label', meta['param']),
                  'unit': meta.get('unit', '')}]
    print("エラー: metadata.json にパラメータ情報がありません（'sweeps'/'param'）")
    sys.exit(1)


def _normalize_result(r, names):
    """新形式('combo'/'dirname')・旧形式（単一パラメータの'requested'）どちらの結果行にも対応する。"""
    if 'combo' in r:
        return r
    name = names[0]
    return {
        'combo': {name: r['requested']},
        'actual': {name: r.get('actual')},
        'saved': r.get('saved', False),
        'dirname': safe_dirname(r['requested']),
    }


def _is_numeric(values):
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('session_dir', nargs='?', default=None,
                         help='param_sweep.py の保存先セッションディレクトリ（省略時は最新を自動選択）')
    parser.add_argument('--model', type=str, default=None, help='YOLOモデルパス（省略時は config.yaml の model.yolo_path）')
    parser.add_argument('--conf', type=float, default=None, help='信頼度閾値（省略時は config.yaml の model.confidence_threshold）')
    args = parser.parse_args()

    cfg = load_config()

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

    meta    = _load_metadata(session_dir)
    sweeps  = _resolve_sweeps(meta)
    names   = [s['param'] for s in sweeps]
    label_map = {s['param']: s.get('label', s['param']) for s in sweeps}
    unit_map  = {s['param']: s.get('unit', '') for s in sweeps}
    combine   = meta.get('combine', 'product' if len(names) > 1 else None)

    results = [_normalize_result(r, names) for r in meta.get('results', [])]
    saved = [r for r in results if r.get('saved')]
    if not saved:
        print("保存済みの撮影データがありません。")
        sys.exit(1)

    model_cfg  = cfg.get('model', {})
    root       = Path(__file__).resolve().parent.parent.parent / 'nyx660_script'
    model_path = args.model or str((root / model_cfg.get('yolo_path', 'model/best.pt')).resolve())
    conf       = args.conf if args.conf is not None else float(model_cfg.get('confidence_threshold', 0.5))

    if not Path(model_path).exists():
        print(f"YOLOモデルが見つかりません: {model_path}")
        sys.exit(1)

    from ultralytics import YOLO
    print(f"モデル読み込み: {model_path}  信頼度閾値: {conf}")
    model = YOLO(model_path)

    print(f"セッション: {session_dir}")
    print(f"パラメータ: {[(n, label_map[n]) for n in names]}\n")

    rows = []
    for r in saved:
        combo = r['combo']
        actual = r.get('actual', {})
        dirname = r['dirname']
        vdir = session_dir / dirname
        color_path = find_result_file(vdir, dirname, 'color', 'png')
        if color_path is None:
            print(f"  警告: {vdir} に color 画像が見つかりません。スキップします。")
            continue

        res = model(str(color_path), conf=conf, verbose=False)[0]
        boxes = res.boxes
        n = len(boxes)
        confs = boxes.conf.cpu().numpy() if n > 0 else []
        avg_conf = float(sum(confs) / n) if n > 0 else 0.0
        max_conf = float(max(confs)) if n > 0 else 0.0
        classes = ','.join(sorted({res.names[int(c)] for c in boxes.cls.cpu().numpy()})) if n > 0 else ''

        annotated = res.plot()
        detected_path = vdir / result_filename(dirname, 'detected', 'jpg')
        cv2.imwrite(str(detected_path), annotated)

        row = dict(combo)
        for name in names:
            row[f'{name}_actual'] = actual.get(name)
        row.update({'n_detections': n, 'avg_conf': avg_conf, 'max_conf': max_conf, 'classes': classes})
        row['_combo'] = combo
        row['_dirname'] = dirname
        rows.append(row)

        label = combo_label(combo, unit_map)
        actual_label = combo_label(actual, unit_map) if actual else ''
        print(f"  {label}  実機: {actual_label}  検出:{n:2d}個  "
              f"avg_conf={avg_conf:.3f}  max_conf={max_conf:.3f}  [{classes}]")

    if not rows:
        print("検出対象の画像がありませんでした。")
        sys.exit(1)

    # --- Auto参考撮影（あれば）も同じモデルで検出しておく（ランキング/CSVには含めない） ---
    ref_row = None
    auto_ref = meta.get('auto_reference')
    if auto_ref and auto_ref.get('saved'):
        ref_dirname = auto_ref['dirname']
        ref_vdir = session_dir / ref_dirname
        ref_color_path = find_result_file(ref_vdir, ref_dirname, 'color', 'png')
        if ref_color_path is None:
            print(f"  警告: {ref_vdir} に color 画像が見つかりません（Auto参考撮影）。スキップします。")
        else:
            res = model(str(ref_color_path), conf=conf, verbose=False)[0]
            boxes = res.boxes
            n = len(boxes)
            confs = boxes.conf.cpu().numpy() if n > 0 else []
            avg_conf = float(sum(confs) / n) if n > 0 else 0.0
            max_conf = float(max(confs)) if n > 0 else 0.0
            classes = ','.join(sorted({res.names[int(c)] for c in boxes.cls.cpu().numpy()})) if n > 0 else ''
            cv2.imwrite(str(ref_vdir / result_filename(ref_dirname, 'detected', 'jpg')), res.plot())

            ref_row = {'_combo': {}, '_dirname': ref_dirname, 'n_detections': n,
                       'avg_conf': avg_conf, 'max_conf': max_conf, 'classes': classes}
            print(f"\n  [AUTO参考] 実機 tof_exposure={auto_ref['actual'].get('tof_exposure')}us "
                  f"color_exposure={auto_ref['actual'].get('color_exposure')}us  検出:{n:2d}個  "
                  f"avg_conf={avg_conf:.3f}  max_conf={max_conf:.3f}  [{classes}]")
            with open(ref_vdir / 'detection.json', 'w') as f:
                json.dump({**auto_ref, 'n_detections': n, 'avg_conf': avg_conf,
                           'max_conf': max_conf, 'classes': classes}, f, indent=2, ensure_ascii=False)

    if len(names) == 1 and _is_numeric([r['_combo'][names[0]] for r in rows]):
        rows.sort(key=lambda r: r['_combo'][names[0]])

    # --- CSV ---
    csv_path = session_dir / 'detection_summary.csv'
    fieldnames = names + [f'{n}_actual' for n in names] + ['n_detections', 'avg_conf', 'max_conf', 'classes']
    with open(csv_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r[k] for k in fieldnames})
    print(f"\nCSV保存: {csv_path}")

    # --- ランキング表示（検出数→平均信頼度の順） ---
    ranked = sorted(rows, key=lambda r: (r['n_detections'], r['avg_conf']), reverse=True)
    print("\n--- ランキング（検出数優先、次いで平均信頼度） ---")
    for i, r in enumerate(ranked[:5], 1):
        print(f"  {i}. {combo_label(r['_combo'], unit_map)}  検出:{r['n_detections']}個  avg_conf={r['avg_conf']:.3f}")
    best = ranked[0]
    print(f"\n推奨: {combo_label(best['_combo'], unit_map)}"
          f"（検出{best['n_detections']}個, 平均信頼度{best['avg_conf']:.3f}）")

    # --- 検出結果の比較画像（BBox描画済み画像を1枚のコンタクトシートに） ---
    grid_shape, ordered, axis_info = montage_grid_for_combos(rows, names, combine, combo_key='_combo')
    ordered_rows = ordered if ordered is not None else rows

    # パラメータ2個・直積の2次元グリッドの場合のみ、行/列見出し（値の昇順）を用意する。
    row_labels = col_labels = row_axis_name = col_axis_name = None
    if axis_info:
        col_labels = [f"{v}{unit_map.get(axis_info['x_name'], '')}" for v in axis_info['x_values']]
        row_labels = [f"{v}{unit_map.get(axis_info['y_name'], '')}" for v in axis_info['y_values']]
        col_axis_name = axis_info['x_name']
        row_axis_name = axis_info['y_name']

    ordered_rows, grid_shape, prepended = with_reference_tile(ordered_rows, grid_shape, ref_row)

    tiles = []
    for r in ordered_rows:
        if r is None:
            tiles.append((None, ''))
            continue
        vdir = session_dir / r['_dirname']
        img_path = vdir / result_filename(r['_dirname'], 'detected', 'jpg')
        # cv2.putTextはASCIIのみ描画可能（日本語は文字化けする）ため英語表記にする
        combo_text = 'AUTO reference' if r is ref_row else combo_label(r['_combo'], unit_map).replace(', ', '\n')
        label = f"{combo_text}\ndet:{r['n_detections']} conf:{r['avg_conf']:.2f}"
        tiles.append((str(img_path) if img_path.exists() else None, label))
    comparison_path = session_dir / 'comparison_detected.png'
    if build_montage(tiles, comparison_path,
                      title=f"detection comparison - {', '.join(names)}",
                      grid_shape=grid_shape,
                      row_labels=row_labels, col_labels=col_labels,
                      row_axis_name=row_axis_name, col_axis_name=col_axis_name):
        print(f"比較画像: {comparison_path}")
    if ref_row and not prepended:
        print(f"参考: AutoのYOLO検出結果は {session_dir / ref_row['_dirname']} を確認してください"
              f"（{len(names)}パラメータの比較グリッドには含めていません）")

    # --- グラフ ---
    if plt is None:
        print("\n警告: matplotlib が無いためグラフは省略します（pip install matplotlib）")
        return

    # matplotlibの既定フォントは日本語グリフを持たないため（環境依存で文字化けする）英語表記にする
    title = f"Detection summary - {', '.join(names)} / {session_dir.name}"
    png_path = session_dir / 'detection_summary.png'

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

    axes[0].plot(x, [r['n_detections'] for r in rows], marker='o', color='steelblue')
    axes[0].set_ylabel('detections')
    axes[0].set_title('Detection count')
    axes[0].grid(True, alpha=0.3)
    axes[0].set_ylim(bottom=0)

    axes[1].plot(x, [r['avg_conf'] for r in rows], marker='o', color='darkorange')
    axes[1].set_ylabel('avg confidence')
    axes[1].set_title('Average confidence')
    axes[1].grid(True, alpha=0.3)
    axes[1].set_ylim(0, 1)
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

    grid_det  = np.full((len(ys), len(xs)), np.nan)
    grid_conf = np.full((len(ys), len(xs)), np.nan)
    for r in rows:
        i, j = yi[r['_combo'][name_y]], xi[r['_combo'][name_x]]
        grid_det[i, j]  = r['n_detections']
        grid_conf[i, j] = r['avg_conf']

    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))
    fig.suptitle(title, fontsize=11)

    for ax, grid, cmap, sub_title, fmt in (
        (axes[0], grid_det, 'Blues', 'Detection count', '{:.0f}'),
        (axes[1], grid_conf, 'Oranges', 'Average confidence', '{:.2f}'),
    ):
        im = ax.imshow(grid, cmap=cmap, aspect='auto')
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
                    ax.text(j, i, fmt.format(grid[i, j]), ha='center', va='center', fontsize=8)
        fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)

    plt.tight_layout()
    fig.savefig(png_path, dpi=150)


def _plot_bar(rows, unit_map, title, png_path):
    x = list(range(len(rows)))
    xticklabels = [combo_label(r['_combo'], unit_map) for r in rows]

    fig, axes = plt.subplots(2, 1, figsize=(max(10, len(rows) * 0.6), 9), sharex=True)
    fig.suptitle(title, fontsize=11)

    axes[0].bar(x, [r['n_detections'] for r in rows], color='steelblue')
    axes[0].set_ylabel('detections')
    axes[0].set_title('Detection count')
    axes[0].grid(True, alpha=0.3, axis='y')

    axes[1].bar(x, [r['avg_conf'] for r in rows], color='darkorange')
    axes[1].set_ylabel('avg confidence')
    axes[1].set_title('Average confidence')
    axes[1].grid(True, alpha=0.3, axis='y')
    axes[1].set_ylim(0, 1)
    axes[1].set_xticks(x)
    axes[1].set_xticklabels(xticklabels, rotation=45, ha='right', fontsize=8)

    plt.tight_layout()
    fig.savefig(png_path, dpi=150)


if __name__ == '__main__':
    main()
