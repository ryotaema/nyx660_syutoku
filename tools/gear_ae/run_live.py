#!/usr/bin/env python3
"""ギア方式 Color 露光制御を実機（ScepterSDK 直接操作）で動かす。

カメラは utils.open_camera() で開き（config.yaml の params_json を適用）、Color 露光を手動にして
段の露光・ゲインを設定する。終了時（Ctrl-C を含む）に開始前の Color 露光設定へ戻す。
機種は productName から自動判別し、gears/<機種>_<環境>.yaml を使う。

使い方:
    # 制御を回す（Ctrl-C で終了）。YOLO は CPU だと1枚約1秒なので、数フレームおきにすることもできる
    python3 tools/gear_ae/run_live.py --env outdoor_day
    python3 tools/gear_ae/run_live.py --env outdoor_day --frames 300 --detect-every 3 --save-every 30

    # 明るさだけで動かす（YOLO なし。「戻す」判定は働かない）
    python3 tools/gear_ae/run_live.py --no-yolo

    # 露光の反映遅れを測る（2つの段を交互に切り替える。制御・YOLO なし）
    python3 tools/gear_ae/run_live.py --latency-test
    python3 tools/gear_ae/run_live.py --latency-test --latency-gears 1,4 --latency-period 15 --latency-cycles 5

出力（config.yaml の output.gear_ae_dir/live/<日時>_<機種>_<環境>[_latency]/）:
    log.csv        フレームごとの frameIndex・段・露光・ゲイン・lum・白飛び率・検出数・conf の合計・判断理由
    events.csv     段の切替ごとの要求値・上限で丸めた値・読み戻し値・設定にかかった時間
    summary.json   デバイス情報・ギア表・実行条件・（反映遅れ測定なら）切替ごとの遅れ
    timeline.png   段・lum・conf の合計の推移
    images/        --save-every のフレームと、段を切り替える直前のフレーム（jpg）
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / 'nyx660_script'))

from backends.sdk_direct import SdkDirectBackend  # noqa: E402
from core.controller import GearController  # noqa: E402
from core.gear_table import find_gear_table, load_gear_table  # noqa: E402
from core.metrics import measure  # noqa: E402
from core.tracker import PersistenceTracker, score  # noqa: E402
from report import plot_timeline, write_csv  # noqa: E402
from utils import load_config  # noqa: E402


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--model', default='auto', help='機種名（ギア表の選択に使う）。auto は productName から判別')
    ap.add_argument('--env', default='outdoor_day', help='環境名（ギア表の選択に使う）')
    ap.add_argument('--gears', type=Path, help='ギア表を直接指定（--model/--env より優先）')
    ap.add_argument('--fps', type=int, help='FPS を上書き（Color 露光の上限が FPS で変わる）')
    ap.add_argument('--start-gear', type=int, help='開始段（省略時はギア表の既定の段）')
    ap.add_argument('--frames', type=int, default=0, help='処理するフレーム数（0 は Ctrl-C まで）')
    ap.add_argument('--no-yolo', action='store_true', help='YOLO を使わない（明るさだけで動かす）')
    ap.add_argument('--detect-every', type=int, default=1, help='YOLO を N フレームおきに実行')
    ap.add_argument('--yolo-model', help='YOLOモデル（省略時は config.yaml の model.yolo_path）')
    ap.add_argument('--conf', type=float, help='YOLO の信頼度閾値（省略時は config.yaml）')
    ap.add_argument('--save-every', type=int, default=0, help='N フレームごとに画像を保存（0 は切替直前のみ）')
    ap.add_argument('--latency-test', action='store_true', help='露光の反映遅れを測る')
    ap.add_argument('--latency-gears', help='反映遅れの測定で交互に切り替える2段（例: 1,4）。省略時は既定の段±1')
    ap.add_argument('--latency-period', type=int, default=15, help='反映遅れの測定で、切り替える間隔（フレーム）')
    ap.add_argument('--latency-cycles', type=int, default=5, help='反映遅れの測定で、往復する回数')
    ap.add_argument('--out', type=Path, help='出力ディレクトリ（省略時は gear_ae_dir/live/...）')
    return ap.parse_args()


def frame_row(f, backend, table, gear, m):
    g = table.gears[gear]
    return dict(frame=f, frame_index=backend.last_frame.get('frame_index'), hw_ts=backend.last_frame.get('hw_ts'),
                wall=round(backend.last_frame.get('wall', 0.0), 3), gear=gear, exposure=g.exposure, gain=g.gain,
                lum=None if m.lum is None else round(m.lum, 1), clip=round(m.clip_frac, 4),
                valid=round(m.valid_frac, 3))


def save_image(out, f, gear, img, tag=''):
    d = out / 'images'
    d.mkdir(exist_ok=True)
    cv2.imwrite(str(d / f'f{f:05d}_g{gear}{tag}.jpg'), img, [cv2.IMWRITE_JPEG_QUALITY, 90])


# ----------------------------------------------------------------------
def run_control(args, cfg, backend, table, out):
    detector = None
    if not args.no_yolo:
        from detector import YoloDetector
        detector = YoloDetector(args.yolo_model, args.conf)
        print(f'YOLO: {detector.model_path} conf={detector.conf}')
    ctl = GearController(table, args.start_gear)
    tracker = PersistenceTracker()
    events = [dict(frame=0, from_gear=None, to_gear=ctl.gear, reason='start', **backend.set_gear(table.gears[ctl.gear]))]
    rows, last_score, f = [], 0.0, 0
    try:
        while args.frames <= 0 or f < args.frames:
            img, _ = backend.read_frame()
            m = measure(img, table.params.min_valid_frac)
            n_det, det_ms = None, None
            if detector is not None and f % args.detect_every == 0:
                t0 = time.monotonic()
                dets = detector.detect(img)
                det_ms = round((time.monotonic() - t0) * 1000, 1)
                n_det = len(dets)
                last_score = score(tracker.update(dets))
            cur = ctl.gear
            dec = ctl.step(m, last_score)
            row = frame_row(f, backend, table, cur, m)
            row.update(n_det=n_det, score=round(last_score, 3), det_ms=det_ms,
                       next_gear=dec.gear, changed=dec.changed, reason=dec.reason)
            rows.append(row)
            if dec.changed or (args.save_every and f % args.save_every == 0):
                save_image(out, f, cur, img, '_before_change' if dec.changed else '')
            if dec.changed:
                applied = backend.set_gear(table.gears[dec.gear])
                events.append(dict(frame=f, from_gear=cur, to_gear=dec.gear, reason=dec.reason, **applied))
                print(f'[{f:5d}] gear {cur} -> {dec.gear}  ({dec.reason})  set={applied}')
            elif f % 10 == 0:
                print(f"[{f:5d}] gear {cur} lum={row['lum']} clip={row['clip']} det={n_det} "
                      f"score={row['score']} ({dec.reason})")
            f += 1
    except KeyboardInterrupt:
        print('中断しました（Ctrl-C）')
    return rows, events, {}


# ----------------------------------------------------------------------
def run_latency(args, cfg, backend, table, out):
    if args.latency_gears:
        a, b = (int(v) for v in args.latency_gears.split(','))
    else:
        a, b = max(0, table.default_gear - 1), min(table.last, table.default_gear + 1)
    if a == b or not (0 <= a <= table.last and 0 <= b <= table.last):
        raise ValueError(f'--latency-gears が不正: {a},{b}（0..{table.last} の異なる2段）')
    period, total = args.latency_period, args.latency_period * 2 * args.latency_cycles
    target = a
    events = [dict(frame=0, from_gear=None, to_gear=a, reason='start', **backend.set_gear(table.gears[a]))]
    rows = []
    try:
        for f in range(total):
            img, _ = backend.read_frame()
            m = measure(img, table.params.min_valid_frac)
            row = frame_row(f, backend, table, target, m)
            row.update(n_det=None, score=0.0)
            rows.append(row)
            if (f + 1) % period == 0 and f + 1 < total:
                prev, target = target, (b if target == a else a)
                save_image(out, f, prev, img, '_before_change')
                applied = backend.set_gear(table.gears[target])
                events.append(dict(frame=f + 1, from_gear=prev, to_gear=target, reason='latency switch', **applied))
            if f % 10 == 0:
                print(f"[{f:4d}] gear {target} lum={row['lum']} frameIndex={row['frame_index']}")
    except KeyboardInterrupt:
        print('中断しました（Ctrl-C）')
    return rows, events, dict(latency=analyze_latency(rows, events[1:], period))


def analyze_latency(rows, switches, period, plateau=5):
    """切替ごとに、lum が切替前後の安定値の中間を超えるまでの読み取り回数と frameIndex の差を求める。"""
    out = []
    lum = [r['lum'] for r in rows]
    for s in switches:
        k0 = s['frame']
        before = [v for v in lum[max(0, k0 - plateau):k0] if v is not None]
        after = [v for v in lum[k0 + period - plateau:k0 + period] if v is not None]
        if not before or not after or k0 >= len(rows):
            continue
        lb, la = float(np.median(before)), float(np.median(after))
        if abs(la - lb) < 5:
            out.append(dict(frame=k0, from_gear=s['from_gear'], to_gear=s['to_gear'], lum_before=lb, lum_after=la,
                            delay_reads=None, delay_frame_index=None, note='明るさの差が小さく判定不能'))
            continue
        mid = (lb + la) / 2
        hit = next((k for k in range(k0, min(len(rows), k0 + period))
                    if lum[k] is not None and (lum[k] - mid) * (la - lb) > 0), None)
        idx_set = s.get('after_frame')
        out.append(dict(frame=k0, from_gear=s['from_gear'], to_gear=s['to_gear'], lum_before=round(lb, 1),
                        lum_after=round(la, 1),
                        delay_reads=None if hit is None else hit - k0,
                        delay_frame_index=None if hit is None or idx_set is None or rows[hit]['frame_index'] is None
                        else rows[hit]['frame_index'] - idx_set))
    reads = [o['delay_reads'] for o in out if o['delay_reads'] is not None]
    return dict(switches=out, delay_reads_max=max(reads) if reads else None,
                delay_reads_median=float(np.median(reads)) if reads else None)


# ----------------------------------------------------------------------
def main():
    args = parse_args()
    cfg = load_config()
    if args.fps:
        cfg['camera']['fps'] = args.fps
    with SdkDirectBackend(cfg) as backend:
        device = backend.device()
        model = backend.detect_model() if args.model == 'auto' else args.model
        print(f"デバイス: {device}  → 機種 {model}")
        table = load_gear_table(args.gears) if args.gears else find_gear_table(HERE / 'gears', model, args.env)
        over = backend.check_gears(table)
        if over:
            print(f'警告: 露光が現在の上限（{backend.max_exposure}us）を超える段は上限に丸めます: {over}')
        mode = '_latency' if args.latency_test else ''
        out = args.out or Path(cfg['output']['gear_ae_dir']) / 'live' / \
            f"{datetime.now():%Y%m%d_%H%M%S}_{table.model}_{table.env}{mode}"
        out.mkdir(parents=True, exist_ok=True)
        print(f'ギア表: {table.source}  出力: {out}')

        runner = run_latency if args.latency_test else run_control
        rows, events, extra = runner(args, cfg, backend, table, out)

    write_csv(out / 'log.csv', rows)
    write_csv(out / 'events.csv', events)
    (out / 'summary.json').write_text(json.dumps(dict(
        device=device, model=table.model, env=table.env, gear_table=table.source,
        gears=[dict(exposure=g.exposure, gain=g.gain) for g in table.gears], default_gear=table.default_gear,
        params={k: v for k, v in vars(table.params).items()}, color_max_exposure=backend.max_exposure,
        fps=cfg['camera'].get('fps'), args={k: str(v) for k, v in vars(args).items()},
        n_frames=len(rows), n_changes=len(events) - 1, **extra), ensure_ascii=False, indent=2))
    if rows:
        plot_timeline(out / 'timeline.png', table, {'live': rows},
                      title=f"{table.model} {table.env}{' latency test' if args.latency_test else ''}")
    if extra.get('latency'):
        lat = extra['latency']
        print(f"反映遅れ（読み取り回数）: 中央値 {lat['delay_reads_median']} / 最大 {lat['delay_reads_max']}"
              f"  → settle_frames は最大値以上にする")
    print(f'出力: {out}')


if __name__ == '__main__':
    main()
