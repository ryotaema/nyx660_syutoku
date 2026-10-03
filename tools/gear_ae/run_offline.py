#!/usr/bin/env python3
"""ギア方式 Color 露光制御を、param_sweep.py の Color スイープ上でオフライン再現する。

スイープの各組合せを「その段で撮れた画像」とみなし、制御が段をどう動かすかをフレームごとに
再現する。同じ画像を繰り返し使うため、実機の時間変化（雲・風など）は含まれない。
段を変えると次のフレームから新しい画像になる（実機の反映遅れは含まない。controller の
settle_frames で待つ）。

使い方:
    # 全ての開始段から再現（既定）。機種は ToF の fx から自動判別、環境は outdoor_day
    python3 tools/gear_ae/run_offline.py <Colorスイープのセッションディレクトリ>

    # 開始段を指定し、悪条件を混ぜる
    python3 tools/gear_ae/run_offline.py <session> --start-gear 0 \\
        --blank-start 20 --drop-prob 0.2 --fp-prob 0.3

悪条件のオプション:
    --blank-start N  最初の N フレームは空だけが写った画像（対象なし・計測不能）にする
    --drop-prob p    各検出を確率 p で落とす（検出のちらつき）
    --fp-prob p      確率 p で1フレームだけの誤検出（conf 0.5〜0.75）を足す

出力（config.yaml の output.gear_ae_dir/offline/<セッション名>_<機種>_<環境>_<日時>/）:
    log_start<k>.csv   フレームごとの段・露光・ゲイン・lum・白飛び率・検出数・conf の合計・判断理由
    gear_reference.csv ギア表の各段の lum・検出数（その段に留まった場合の参考値）
    summary.json       開始段ごとの最終段・切替回数・最後に段を変えたフレーム など
    timeline.png       開始段ごとの段・lum・conf の合計の推移
"""

import argparse
import json
import random
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / 'nyx660_script'))

from backends.replay import ReplayBackend  # noqa: E402
from core.controller import GearController  # noqa: E402
from core.gear_table import find_gear_table, load_gear_table  # noqa: E402
from core.metrics import measure  # noqa: E402
from core.tracker import Detection, PersistenceTracker, score  # noqa: E402
from detector import CachedDetector, YoloDetector  # noqa: E402
from report import plot_timeline, write_csv  # noqa: E402
from utils import load_config  # noqa: E402

# 「空だけが写った」画像（明るく色の薄い一様な画像）。metrics では計測不能になる
SKY_IMAGE = np.full((1200, 1600, 3), (235, 228, 222), np.uint8)


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('session_dir', type=Path, help='Color スイープ（color_exposure × color_gain）のセッション')
    ap.add_argument('--model', default='auto', help='機種名（ギア表の選択に使う）。auto は ToF fx から判別')
    ap.add_argument('--env', default='outdoor_day', help='環境名（ギア表の選択に使う）')
    ap.add_argument('--gears', type=Path, help='ギア表を直接指定（--model/--env より優先）')
    ap.add_argument('--start-gear', default='all', help="開始段（整数）または 'all'")
    ap.add_argument('--frames', type=int, default=120)
    ap.add_argument('--blank-start', type=int, default=0)
    ap.add_argument('--drop-prob', type=float, default=0.0)
    ap.add_argument('--fp-prob', type=float, default=0.0)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--yolo-model', help='YOLOモデル（省略時は config.yaml の model.yolo_path）')
    ap.add_argument('--conf', type=float, help='YOLO の信頼度閾値（省略時は config.yaml）')
    ap.add_argument('--out', type=Path, help='出力ディレクトリ（省略時は gear_ae_dir/offline/...）')
    return ap.parse_args()


def run_once(table, backend, detector, start_gear, args, rng):
    ctl = GearController(table, start_gear)
    tracker = PersistenceTracker()
    backend.set_gear(table.gears[ctl.gear])
    rows = []
    for f in range(args.frames):
        if f < args.blank_start:
            img, dets = SKY_IMAGE, []
        else:
            img, key = backend.read_frame()
            dets = list(detector.detect(img, key))
        dets = [d for d in dets if rng.random() >= args.drop_prob]
        if rng.random() < args.fp_prob:
            h, w = img.shape[:2]
            x, y = rng.uniform(0, w - 80), rng.uniform(0, h - 80)
            dets.append(Detection(box=(x, y, x + 80, y + 80), conf=rng.uniform(0.5, 0.75)))
        m = measure(img, table.params.min_valid_frac)
        sc = score(tracker.update(dets))
        cur = ctl.gear  # このフレームを撮ったときの段
        dec = ctl.step(m, sc)
        rows.append(dict(frame=f, gear=cur, exposure=table.gears[cur].exposure, gain=table.gears[cur].gain,
                         lum=None if m.lum is None else round(m.lum, 1), clip=round(m.clip_frac, 4),
                         valid=round(m.valid_frac, 3), n_det=len(dets), score=round(sc, 3),
                         next_gear=dec.gear, changed=dec.changed, reason=dec.reason))
        if dec.changed:
            backend.set_gear(table.gears[dec.gear])
    return rows


def gear_reference(table, backend, detector):
    out = []
    for i, g in enumerate(table.gears):
        backend.set_gear(g)
        img, key = backend.read_frame()
        m = measure(img, table.params.min_valid_frac)
        dets = detector.detect(img, key)
        out.append(dict(gear=i, exposure=g.exposure, gain=g.gain,
                        lum=None if m.lum is None else round(m.lum, 1), clip=round(m.clip_frac, 4),
                        n_det=len(dets), score=round(sum(d.conf for d in dets), 3)))
    return out


def summarize(table, rows, ref):
    changes = [r for r in rows if r['changed']]
    final = rows[-1]['next_gear']
    return dict(start_gear=rows[0]['gear'], final_gear=final,
                final_exposure=table.gears[final].exposure, final_gain=table.gears[final].gain,
                final_lum=ref[final]['lum'], final_n_det=ref[final]['n_det'],
                n_changes=len(changes), last_change_frame=changes[-1]['frame'] if changes else None,
                reasons=[f"{r['frame']}:{r['gear']}->{r['next_gear']} {r['reason']}" for r in changes])


def main():
    args = parse_args()
    cfg = load_config()
    backend = ReplayBackend(args.session_dir)
    model = backend.detect_model() if args.model == 'auto' else args.model
    gears_dir = HERE / 'gears'
    table = load_gear_table(args.gears) if args.gears else find_gear_table(gears_dir, model, args.env)
    backend.check_gears(table)

    out = args.out or Path(cfg['output']['gear_ae_dir']) / 'offline' / \
        f"{args.session_dir.name}_{table.model}_{table.env}_{datetime.now():%Y%m%d_%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    yolo = YoloDetector(args.yolo_model, args.conf)
    detector = CachedDetector(yolo, Path(cfg['output']['gear_ae_dir']) / 'det_cache' / f'{args.session_dir.name}.json')

    ref = gear_reference(table, backend, detector)
    starts = range(len(table.gears)) if args.start_gear == 'all' else [int(args.start_gear)]
    runs, summaries = {}, []
    for s in starts:
        rows = run_once(table, backend, detector, s, args, random.Random(args.seed + s))
        runs[s] = rows
        summaries.append(summarize(table, rows, ref))
        write_csv(out / f'log_start{s}.csv', rows)
    detector.save()

    write_csv(out / 'gear_reference.csv', ref)
    (out / 'summary.json').write_text(json.dumps(dict(
        session=str(args.session_dir), model=table.model, env=table.env, gear_table=table.source,
        yolo=yolo.model_path, conf=yolo.conf, frames=args.frames, blank_start=args.blank_start,
        drop_prob=args.drop_prob, fp_prob=args.fp_prob, seed=args.seed,
        gear_reference=ref, runs=summaries), ensure_ascii=False, indent=2))
    plot_timeline(out / 'timeline.png', table, {f'start {s}': rows for s, rows in runs.items()},
                  gear_labels=[f"{i}: {g.exposure}us g{g.gain:g} (det {ref[i]['n_det']})" for i, g in enumerate(table.gears)])

    print(f'機種 {table.model} / 環境 {table.env} / ギア表 {table.source}')
    print('段ごとの参考値（その段に留まった場合）:')
    for r in ref:
        print(f"  {r['gear']}: {r['exposure']:5d}us gain{r['gain']:<4g} lum={r['lum']} clip={r['clip']} det={r['n_det']}")
    print('開始段ごとの結果:')
    for s in summaries:
        print(f"  start {s['start_gear']} -> final {s['final_gear']} (lum={s['final_lum']}, det={s['final_n_det']}) "
              f"changes={s['n_changes']} last_change={s['last_change_frame']}")
    print(f'出力: {out}')


if __name__ == '__main__':
    main()
