#!/usr/bin/env python3
"""NYX660のカメラパラメータを1つ、または複数同時に振りながら
color/depth/depth_colormap（・点群）を撮り比べるツール。

露光時間やフィルタ閾値などを変えたときに画質・深度がどう変わるかを
値の組み合わせごとのフォルダに保存し、後から見比べられるようにする。撮影後は
detect_eval.py でYOLO検出結果を集計し、どの値・どの組み合わせが有用か判断できる。

対応パラメータ一覧の表示:
    python3 tools/param_tune/param_sweep.py --list

値の指定方法（--values / GUIの「値」欄）:
    "1000,2000,3000"   カンマ区切りで値を列挙
    "1:10:1"            start:stop:step（stopを含む等差数列）。例:
                         1:10:1 → 1,2,3,4,5,6,7,8,9,10（1から10まで1刻み）
                         0:100:20 → 0,20,40,60,80,100

使い方（例）:
    # ToF露光時間を 1000,2000,3000,5000,8000 us で振って撮影（自動モード）
    python3 tools/param_tune/param_sweep.py --param tof_exposure \\
        --values 1000,2000,3000,5000,8000

    # 時間フィルタ閾値を 1〜10 まで1刻みで振る（start:stop:step）
    python3 tools/param_tune/param_sweep.py --param time_filter_threshold \\
        --values 1:10:1

    # 複数パラメータを同時に振る（--param/--valuesを対で複数回指定）。
    # 既定は全組み合わせ（直積・product）: ToF露光5値 × 時間フィルタ3値 = 15通り撮影
    python3 tools/param_tune/param_sweep.py \\
        --param tof_exposure --values 1000,3000,5000,8000,12000 \\
        --param time_filter_threshold --values 1,3,5

    # 値の個数を揃えて1対1で組にする場合は --combine zip
    # （tof_exposure=1000&threshold=1, 3000&3, 5000&5 の3通りのみ撮影）
    python3 tools/param_tune/param_sweep.py \\
        --param tof_exposure --values 1000,3000,5000 \\
        --param time_filter_threshold --values 1,3,5 --combine zip

    # Colorをマニュアル露光にしてgainも振る、点群フィルタ（空間フィルタ）も対象にできる
    python3 tools/param_tune/param_sweep.py --param color_gain --values 1,2,4,8,16
    python3 tools/param_tune/param_sweep.py --param spatial_filter --values 0,1

    # Color自動露光の上限を振る（color_exposure/color_gainと違いAutoモードのまま使う）
    python3 tools/param_tune/param_sweep.py --param color_aec_max_exposure_time --values 20000,50000,100000

    # HDR/WDRのON/OFFを比較する（各フレームの露光時間はSDK既定・直前設定のまま）
    python3 tools/param_tune/param_sweep.py --param hdr_mode --values 0,1
    python3 tools/param_tune/param_sweep.py --param wdr_mode --values 0,1

    # param_sweep_gui.py で作成した設定ファイルから実行
    python3 tools/param_tune/param_sweep.py --config configs/20260818_153000_tof_exposure.json

    # Auto参考撮影（既定でON）を省略する場合
    python3 tools/param_tune/param_sweep.py --param tof_exposure --values 1000,3000,5000 --no-auto-reference

    # 露光時間の要求値がFPS上限を超える場合、自動でFPSを下げながら撮影を続ける
    python3 tools/param_tune/param_sweep.py --param tof_exposure --values 1000,10000,50000 \\
        --fps 30 --auto-fps-adjust

Auto参考撮影について:
    既定で、スイープ開始直前にToF・Color両方をAuto露光にして1枚だけ参考撮影する
    （auto_reference/ フォルダに保存）。カメラが自動調整で選ぶ露光値を基準として
    手動で振った値と見比べるための参考データで、撮影は1回のみ（スイープはしない）。
    撮影後は元のexposure control mode/露光時間に戻してからスイープ本体を開始する。
    --no-auto-reference で無効化できる。

FPS自動調整について（--auto-fps-adjust / GUIの「FPS自動調整」トグル）:
    tof_exposure/color_exposure/color_aec_max_exposure_time の要求値が現在のFPSでの
    実機露光上限（scGetMaxExposureTime、FPS依存でSDK非公開）を超える場合、既定では
    警告を出すだけで撮影を続行し、-105(SC_CMD_SYNC_TIME_OUT)で失敗する。
    このフラグを有効にすると、超過を検知した時点でストリームを一旦止めてFPSを
    30→25→20→15→10→6→5→3→2→1 の順に段階的に下げて再開し、値が上限に収まる
    FPSまで自動的に下げてから撮影を続ける。一度下げたFPSはスイープ終了まで
    そのままにする（露光値は昇順で振ることが多く、都度上げ下げしてストリームを
    再起動するより効率的なため）。基準FPSは --fps（GUIではプルダウン、15 or 30）
    で指定する。

GUIでの設定:
    python3 tools/param_tune/param_sweep_gui.py
    振るパラメータ（複数追加可）・値の範囲・組み合わせ方（直積/対応）・
    待機フレーム数・振らない他パラメータの基準値をフォームで設定し、
    そのまま撮影を開始できる（内部でこのスクリプトを --config 付きで呼び出す）。

保存先:
    output.param_tune_dir（既定 ../data/param_tune）/<YYMMDD>/<prefix>_<param1+param2...>[_<tag>]/
        intrinsics.json
        metadata.json                    # パラメータ名・要求値/実機読み戻し値・基準値の一覧
        comparison_color.png             # 全組み合わせ（+Auto参考撮影）のcolorを1枚に並べた比較画像
        comparison_depth_colormap.png    # 同上、depth_colormap版
        auto_reference/                  # ToF・Color両方Auto露光での参考撮影（1枚のみ、既定でON）
            auto_reference_color.png
            auto_reference_depth.png
            auto_reference_depth_colormap.png
        <param1>-<値1>_<param2>-<値2>/                      # 組み合わせごとのフォルダ
            <param1>-<値1>_<param2>-<値2>_color.png          # ファイル名にも組み合わせを埋め込む
            <param1>-<値1>_<param2>-<値2>_depth.png           # （フォルダ外にコピーしても判別できるように）
            <param1>-<値1>_<param2>-<値2>_depth_colormap.png
            <param1>-<値1>_<param2>-<値2>_pointcloud.ply     # --pointcloud 指定時のみ

comparison_*.png はパラメータ1個なら値の昇順で1行、パラメータ2個で直積(product)なら
2次元グリッド（行/列がそれぞれのパラメータ値）に自動で並べる。それ以外（3個以上 or zip）は
撮影順の自動グリッド。detect_eval.py もYOLO検出結果を同様にcomparison_detected.pngへまとめる。

振らない他のパラメータは config.yaml の camera.params_json（現行プロファイル）の値のまま
固定されるが、--config の baseline_overrides でその基準値も明示的に上書きできる。
"""

import sys
import gc
import json
import itertools
import cv2
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / 'nyx660_script'))

from common import (
    PARAM_META, parse_values, cast_value, combo_dirname, combo_label,
    result_filename, build_montage, montage_grid_for_combos, with_reference_tile,
)
from utils import (
    load_config, build_parser, apply_args, init_sdk, open_camera, close_camera,
    extract_depth, extract_color, make_depth_colormap, save_ply, save_intrinsics, Session,
)

# FPS自動調整（--auto-fps-adjust）で段階的に下げていく候補（降順）。
# 露光上限の目安（プロファイル既定FPS=30）: tof≈3万us、color≈3.2万us
# （common.py の tof_exposure/color_exposure の hint 参照。FPSを下げるとおよそ反比例して広がる）。
_FPS_LADDER = [30, 25, 20, 15, 10, 6, 5, 3, 2, 1]

# FPS自動調整の対象となるパラメータ名と、対応するSensorType（main()内で解決）。
_EXPOSURE_LIMIT_PARAMS = ('tof_exposure', 'color_exposure', 'color_aec_max_exposure_time')


def _build_arg_parser():
    p = build_parser()
    p.add_argument('--config', type=str, default=None, metavar='PATH',
                    help='param_sweep_gui.py で保存した設定JSON。CLI引数はこの設定より優先される')
    p.add_argument('--param', action='append', default=None, metavar='NAME',
                    help='振るパラメータ名（--list で一覧表示）。複数指定すると--valuesと'
                         '順番に対になり、複数パラメータの組み合わせを撮影する')
    p.add_argument('--values', action='append', default=None, metavar='SPEC',
                    help='"1000,2000,3000" または "start:stop:step"（stop含む）。'
                         '--param と同じ回数・同じ順番で指定する')
    p.add_argument('--combine', choices=['product', 'zip'], default=None,
                    help='複数パラメータ指定時の組み合わせ方: product=全組み合わせ（既定）'
                         ' | zip=同じ順番同士を1対1で対応させる（値の個数を揃える必要あり）')
    p.add_argument('--mode', choices=['auto', 'manual'], default=None,
                    help='auto: 値ごとに自動で撮影 | manual: プレビューを見て[s]で撮影・[n]でスキップ'
                         '（既定: auto）')
    p.add_argument('--warmup-frames', type=int, default=None, metavar='N',
                    help='パラメータ変更後、撮影前に読み捨てるフレーム数（既定: 10）'
                         '。露光・時間フィルタの安定待ち')
    p.add_argument('--pointcloud', action='store_true',
                    help='値ごとに点群(.ply)も保存する（既定はcolor/depthのみ）')
    p.add_argument('--pause', type=float, default=None, metavar='SEC',
                    help='autoモードで撮影後に結果を表示しておく秒数（既定: 0.3）')
    p.add_argument('--auto-reference', dest='auto_reference', action='store_true', default=None,
                    help='ToF/Color両方をAuto露光にした参考撮影を最初に1回だけ追加する（既定: ON）')
    p.add_argument('--no-auto-reference', dest='auto_reference', action='store_false',
                    help='Auto露光の参考撮影を行わない')
    p.add_argument('--auto-fps-adjust', dest='auto_fps_adjust', action='store_true', default=None,
                    help='露光値(tof_exposure/color_exposure/color_aec_max_exposure_time)が'
                         '現在のFPSでの上限を超える場合、撮影を止めずにFPSを段階的に下げてから'
                         '続行する（既定: OFF＝警告のみで続行し失敗する場合がある）')
    p.add_argument('--no-auto-fps-adjust', dest='auto_fps_adjust', action='store_false',
                    help='FPS自動調整をしない（既定の動作）')
    p.add_argument('--list', action='store_true',
                    help='調整可能なパラメータの一覧を表示して終了する')
    return p


def _print_param_list():
    print("調整可能なパラメータ:")
    for name, meta in PARAM_META.items():
        unit = f" ({meta['unit']})" if meta['unit'] else ""
        print(f"  {name:<30} {meta['label']}{unit}  [{meta['type']}]")
        print(f"      {meta['hint']}")


def _resolve_sweeps(args, file_cfg, parser):
    """CLI引数（複数の--param/--values）と--config由来のsweep定義をマージし、
    [{'param': name, 'values': [...]}] の形にそろえる。CLIが指定されていればCLI優先。
    """
    if args.param:
        values_list = args.values or []
        if len(args.param) != len(values_list):
            parser.error("--param と --values は同じ回数、対になるように指定してください")
        raw_sweeps = [{'param': p, 'values': v} for p, v in zip(args.param, values_list)]
    else:
        raw_sweeps = file_cfg.get('sweeps')
        if not raw_sweeps and file_cfg.get('param'):
            raw_sweeps = [{'param': file_cfg['param'], 'values': file_cfg.get('values')}]

    if not raw_sweeps:
        parser.error("--param/--values が必要です（--list で一覧表示、または --config を指定）")

    sweep_specs = []
    seen = set()
    for s in raw_sweeps:
        name = s.get('param')
        if name not in PARAM_META:
            parser.error(f"--param が不正です: {name!r}（--list で一覧表示）\n"
                          f"選択肢: {', '.join(PARAM_META)}")
        if name in seen:
            parser.error(f"同じパラメータを複数回指定しています: {name}")
        seen.add(name)
        raw_values = s.get('values')
        if not raw_values:
            parser.error(f"--values が必要です（{name}）")
        values = list(raw_values) if isinstance(raw_values, (list, tuple)) else parse_values(raw_values)
        sweep_specs.append({'param': name, 'values': values})
    return sweep_specs


def _build_combos(sweep_specs, combine, parser):
    names = [s['param'] for s in sweep_specs]
    if combine == 'zip':
        lengths = {len(s['values']) for s in sweep_specs}
        if len(lengths) != 1:
            parser.error("--combine zip では全パラメータの値の個数を揃えてください"
                          f"（現在: {[len(s['values']) for s in sweep_specs]}）")
        n = lengths.pop()
        return [{name: sweep_specs[i]['values'][j] for i, name in enumerate(names)} for j in range(n)]
    return [dict(zip(names, vals)) for vals in itertools.product(*(s['values'] for s in sweep_specs))]


def _warn_exposure_range(cam, sweep_specs, ScSensorType, auto_fps_adjust):
    """tof_exposure/color_exposureが振られている場合、現在のFPSでの実機上限
    （scGetMaxExposureTime）を超える要求値がないか撮影前にチェックして警告する。
    露光上限はFPS依存でSDK非公開のため、大量の組み合わせが軒並み-105
    (SC_CMD_SYNC_TIME_OUT)で失敗してから気づく、という事態を避けるためのもの。
    auto_fps_adjust が有効な場合は実際に撮影中にFPSを下げて対応するため、
    ここでは「失敗する」ではなく「自動調整される」という告知に変える。
    """
    sensor_for = {'tof_exposure': ScSensorType.SC_TOF_SENSOR, 'color_exposure': ScSensorType.SC_COLOR_SENSOR}
    for s in sweep_specs:
        sensor = sensor_for.get(s['param'])
        if sensor is None:
            continue
        ret, max_us = cam.scGetMaxExposureTime(sensor)
        if ret != 0:
            continue
        print(f"  {s['param']}: 現在のFPSでの実機露光上限 ≈ {max_us}us")
        over = [v for v in s['values'] if isinstance(v, (int, float)) and v > max_us]
        if over:
            if auto_fps_adjust:
                print(f"    情報: 上限を超える要求値があります: {over}"
                      f"（FPS自動調整が有効なので、撮影中に該当する値でFPSを段階的に下げます）")
            else:
                print(f"    警告: 上限を超える要求値があります（撮影は続行しますが失敗します）: {over}")
                print(f"          上限はFPSに依存します。長い露光が必要な場合は --fps でフレームレートを"
                      f"下げてから再実行するか、--auto-fps-adjust（GUIなら「FPS自動調整」トグル）を"
                      f"有効にしてください（例: FPS 30→5 で上限がおよそ6倍に広がります）")


def main():
    parser = _build_arg_parser()
    args = parser.parse_args()

    if args.list:
        _print_param_list()
        return

    file_cfg = {}
    if args.config:
        with open(args.config) as f:
            file_cfg = json.load(f)

    sweep_specs = _resolve_sweeps(args, file_cfg, parser)
    names = [s['param'] for s in sweep_specs]
    combine = args.combine or file_cfg.get('combine', 'product')
    combos = _build_combos(sweep_specs, combine, parser)

    mode           = args.mode if args.mode is not None else file_cfg.get('mode', 'auto')
    warmup_frames  = args.warmup_frames if args.warmup_frames is not None else int(file_cfg.get('warmup_frames', 10))
    pause          = args.pause if args.pause is not None else float(file_cfg.get('pause', 0.3))
    pointcloud     = args.pointcloud or bool(file_cfg.get('pointcloud', False))
    auto_reference = args.auto_reference if args.auto_reference is not None else bool(file_cfg.get('auto_reference', True))
    auto_fps_adjust = args.auto_fps_adjust if args.auto_fps_adjust is not None \
        else bool(file_cfg.get('auto_fps_adjust', False))
    user_tag       = args.tag or file_cfg.get('tag')
    baseline_overrides = file_cfg.get('baseline_overrides') or {}

    unknown = [k for k in baseline_overrides if k not in PARAM_META]
    if unknown:
        parser.error(f"baseline_overrides に未知のパラメータがあります: {unknown}")

    if len(combos) > 50:
        print(f"警告: {len(combos)} 通りの組み合わせを撮影します。時間がかかる場合があります。")

    # --config の 'fps'（GUIの基準FPSプルダウン）は --fps より優先度は低い
    # （CLI引数が明示されていれば常にCLI優先、という他の設定項目と同じ規則）。
    if args.fps is None and file_cfg.get('fps') is not None:
        args.fps = int(file_cfg['fps'])

    cfg = apply_args(load_config(), args)
    init_sdk(cfg)

    from API.ScepterDS_enums import ScFrameType, ScSensorType, ScExposureControlMode
    from API.ScepterDS_types import (
        ScTimeFilterParams, ScConfidenceFilterParams, ScFlyingPixelFilterParams, ScIRGMMCorrectionParams,
    )
    from ctypes import c_uint16, c_int32, c_uint8, c_float, c_bool

    # 実際に適用中のFPS（auto_fps_adjustで下げていくと変わる）。
    # dictにしているのはネストした関数から書き換えるため（nonlocalの代替）。
    fps_state = {'fps': int(cfg['camera'].get('fps', 30))}

    def _check(name, ret):
        if ret == 0:
            return
        if ret == -105:
            # SC_CMD_SYNC_TIME_OUT: コマンド自体は受理されたが確認応答がタイムアウト。
            # 露光時間設定でよく起きるのは、現在のFPSで実現できる上限を超える値を
            # 要求した場合（device側が値を確定できず応答が返らない）。この場合、
            # 実機の値は変更前のまま（クランプ）されることが多い（実機確認済み）。
            print(f"警告: {name} failed: -105 (SC_CMD_SYNC_TIME_OUT: 確認応答タイムアウト。"
                  "露光時間の場合は現在のFPSでの上限を超えた値を要求している可能性が高い。"
                  "--fps を下げると上限が上がる)")
        else:
            print(f"警告: {name} failed: {ret}")

    def _ensure_manual(cam, sensor_type, label):
        _check(f"scSetExposureControlMode({label})", cam.scSetExposureControlMode(
            sensor_type, ScExposureControlMode.SC_EXPOSURE_CONTROL_MODE_MANUAL))

    def _set_tof_exposure(cam, value):
        _ensure_manual(cam, ScSensorType.SC_TOF_SENSOR, 'ToF')
        _check("scSetExposureTime(ToF)", cam.scSetExposureTime(ScSensorType.SC_TOF_SENSOR, c_int32(value)))

    def _get_tof_exposure(cam):
        return cam.scGetExposureTime(ScSensorType.SC_TOF_SENSOR)[1]

    def _set_color_exposure(cam, value):
        _ensure_manual(cam, ScSensorType.SC_COLOR_SENSOR, 'Color')
        _check("scSetExposureTime(Color)", cam.scSetExposureTime(ScSensorType.SC_COLOR_SENSOR, c_int32(value)))

    def _get_color_exposure(cam):
        return cam.scGetExposureTime(ScSensorType.SC_COLOR_SENSOR)[1]

    def _set_color_gain(cam, value):
        # gainはColor露光がManualの時のみ有効（SDKサンプル ColorExposureTimeSetGet 準拠）
        _ensure_manual(cam, ScSensorType.SC_COLOR_SENSOR, 'Color')
        _check("scSetColorGain", cam.scSetColorGain(c_float(value)))

    def _get_color_gain(cam):
        return cam.scGetColorGain()[1]

    def _set_color_aec_max_exposure(cam, value):
        # 自動露光の上限なのでColor露光はAutoに戻す（color_exposure/color_gainとは逆）
        _check("scSetExposureControlMode(Color)", cam.scSetExposureControlMode(
            ScSensorType.SC_COLOR_SENSOR, ScExposureControlMode.SC_EXPOSURE_CONTROL_MODE_AUTO))
        _check("scSetColorAECMaxExposureTime", cam.scSetColorAECMaxExposureTime(c_int32(value)))

    def _get_color_aec_max_exposure(cam):
        return cam.scGetColorAECMaxExposureTime()[1]

    def _ensure_exposure_fits(cam, sensor, value, label):
        """value(us)が現在のFPSでの実機露光上限（scGetMaxExposureTime）を超える場合、
        auto_fps_adjust が有効ならストリームを一旦止めてFPSを _FPS_LADDER に沿って
        段階的に下げ、値が収まるところで再開する。無効ならログのみ（従来動作）。

        FPSは一度下げたらスイープ終了までそのまま（元に戻さない）。露光値は昇順で
        振ることが多く、都度上げ下げしてストリーム再起動を繰り返すより効率的なため。
        """
        ret, max_us = cam.scGetMaxExposureTime(sensor)
        if ret != 0 or value <= max_us:
            return
        if not auto_fps_adjust:
            print(f"警告: {label} {value}us は現在のFPS({fps_state['fps']})での実機上限"
                  f"（≈{max_us}us）を超えています。撮影は続行しますが失敗する可能性があります"
                  f"（--auto-fps-adjust で自動調整できます）")
            return
        for f in [x for x in _FPS_LADDER if x < fps_state['fps']]:
            print(f"情報: {label} {value}us が現在のFPS({fps_state['fps']})での上限（≈{max_us}us）を"
                  f"超えるため、FPSを{f}に下げて撮影を続けます")
            ret = cam.scStopStream()
            if ret != 0:
                print(f"警告: scStopStream failed: {ret}（FPS変更を中止します）")
                return
            cam.scSetFrameRate(c_uint8(f))
            ret = cam.scStartStream()
            if ret != 0:
                print(f"警告: scStartStream failed: {ret}（FPS変更を反映できませんでした）")
                return
            fps_state['fps'] = f
            ret, max_us = cam.scGetMaxExposureTime(sensor)
            if ret == 0 and value <= max_us:
                print(f"  → FPS {f} で上限 ≈{max_us}us に収まりました")
                return
        print(f"警告: {label} {value}us はFPSを{fps_state['fps']}まで下げても"
              f"上限（≈{max_us}us）を超えています。この値は撮影に失敗する可能性があります")

    _exposure_sensor_of = {}  # PARAM_META名 -> ScSensorType（main()内でしか解決できないためここで構築）

    def _ensure_exposure_fits_for(cam, name, value):
        sensor = _exposure_sensor_of.get(name)
        if sensor is not None:
            _ensure_exposure_fits(cam, sensor, value, PARAM_META[name]['label'])

    def _set_time_filter(cam, value):
        p = ScTimeFilterParams()
        p.enable = True
        p.threshold = value
        _check("scSetTimeFilterParams", cam.scSetTimeFilterParams(p))

    def _get_time_filter(cam):
        return cam.scGetTimeFilterParams()[1].threshold

    def _set_confidence_filter(cam, value):
        p = ScConfidenceFilterParams()
        p.enable = True
        p.threshold = value
        _check("scSetConfidenceFilterParams", cam.scSetConfidenceFilterParams(p))

    def _get_confidence_filter(cam):
        return cam.scGetConfidenceFilterParams()[1].threshold

    def _set_flying_pixel_filter(cam, value):
        p = ScFlyingPixelFilterParams()
        p.enable = True
        p.threshold = value
        _check("scSetFlyingPixelFilterParams", cam.scSetFlyingPixelFilterParams(p))

    def _get_flying_pixel_filter(cam):
        return cam.scGetFlyingPixelFilterParams()[1].threshold

    def _set_spatial_filter(cam, value):
        _check("scSetSpatialFilterEnabled", cam.scSetSpatialFilterEnabled(c_bool(value)))

    def _get_spatial_filter(cam):
        return cam.scGetSpatialFilterEnabled()[1]

    def _set_fillhole_filter(cam, value):
        _check("scSetFillHoleFilterEnabled", cam.scSetFillHoleFilterEnabled(c_bool(value)))

    def _get_fillhole_filter(cam):
        return cam.scGetFillHoleFilterEnabled()[1]

    def _set_ir_gmm_gain(cam, value):
        _check("scSetIRGMMGain", cam.scSetIRGMMGain(c_uint8(value)))

    def _get_ir_gmm_gain(cam):
        return cam.scGetIRGMMGain()[1]

    def _set_ir_gmm_correction_threshold(cam, value):
        p = ScIRGMMCorrectionParams()
        p.enable = True
        p.threshold = value
        _check("scSetIRGMMCorrection", cam.scSetIRGMMCorrection(p))

    def _get_ir_gmm_correction_threshold(cam):
        return cam.scGetIRGMMCorrection()[1].threshold

    def _set_hdr_mode(cam, value):
        _check("scSetHDRModeEnabled", cam.scSetHDRModeEnabled(c_bool(value)))

    def _get_hdr_mode(cam):
        return cam.scGetHDRModeEnabled()[1]

    def _set_wdr_mode(cam, value):
        _check("scSetWDRModeEnabled", cam.scSetWDRModeEnabled(c_bool(value)))

    def _get_wdr_mode(cam):
        return cam.scGetWDRModeEnabled()[1]

    _exposure_sensor_of.update({
        'tof_exposure': ScSensorType.SC_TOF_SENSOR,
        'color_exposure': ScSensorType.SC_COLOR_SENSOR,
        'color_aec_max_exposure_time': ScSensorType.SC_COLOR_SENSOR,
    })

    _setters = {
        'tof_exposure': _set_tof_exposure,
        'color_exposure': _set_color_exposure,
        'color_gain': _set_color_gain,
        'color_aec_max_exposure_time': _set_color_aec_max_exposure,
        'time_filter_threshold': _set_time_filter,
        'confidence_filter_threshold': _set_confidence_filter,
        'flying_pixel_filter_threshold': _set_flying_pixel_filter,
        'spatial_filter': _set_spatial_filter,
        'fillhole_filter': _set_fillhole_filter,
        'ir_gmm_gain': _set_ir_gmm_gain,
        'ir_gmm_correction_threshold': _set_ir_gmm_correction_threshold,
        'hdr_mode': _set_hdr_mode,
        'wdr_mode': _set_wdr_mode,
    }
    _getters = {
        'tof_exposure': _get_tof_exposure,
        'color_exposure': _get_color_exposure,
        'color_gain': _get_color_gain,
        'color_aec_max_exposure_time': _get_color_aec_max_exposure,
        'time_filter_threshold': _get_time_filter,
        'confidence_filter_threshold': _get_confidence_filter,
        'flying_pixel_filter_threshold': _get_flying_pixel_filter,
        'spatial_filter': _get_spatial_filter,
        'fillhole_filter': _get_fillhole_filter,
        'ir_gmm_gain': _get_ir_gmm_gain,
        'ir_gmm_correction_threshold': _get_ir_gmm_correction_threshold,
        'hdr_mode': _get_hdr_mode,
        'wdr_mode': _get_wdr_mode,
    }
    param_specs = {name: {**meta, 'setter': _setters[name], 'getter': _getters[name]}
                   for name, meta in PARAM_META.items()}

    def apply_param(cam, name, value):
        casted = cast_value(name, value)
        if name in _EXPOSURE_LIMIT_PARAMS:
            _ensure_exposure_fits_for(cam, name, casted)
        param_specs[name]['setter'](cam, casted)
        return casted

    unit_map = {name: param_specs[name]['unit'] for name in PARAM_META}
    depth_alpha = cfg['camera'].get('depth_alpha', 0.4)

    dir_tag = "+".join(names) if not user_tag else f"{'+'.join(names)}_{user_tag}"
    session = Session(cfg['output']['param_tune_dir'], tag=dir_tag)
    print(f"保存先: {session.dir}")
    print(f"パラメータ: {[(n, param_specs[n]['label']) for n in names]}")
    print(f"組み合わせ数: {len(combos)}（combine={combine}）")
    print(f"モード: {mode}  warmup: {warmup_frames}フレーム  点群: {'ON' if pointcloud else 'OFF'}"
          f"  Auto参考撮影: {'ON' if auto_reference else 'OFF'}"
          f"  FPS自動調整: {'ON' if auto_fps_adjust else 'OFF'}（基準FPS={fps_state['fps']}）")
    if baseline_overrides:
        print(f"基準値の上書き: {baseline_overrides}")

    try:
        cam = open_camera(cfg)
    except RuntimeError as e:
        print(f"エラー: {e}")
        sys.exit(1)

    save_intrinsics(cam, str(session.dir))

    _warn_exposure_range(cam, sweep_specs, ScSensorType, auto_fps_adjust)

    for bname, bvalue in baseline_overrides.items():
        if bname in names:
            print(f"情報: baseline_overrides の {bname} はスイープ対象のため無視します")
            continue
        apply_param(cam, bname, bvalue)

    def grab_frame():
        ret, frameready = cam.scGetFrameReady(c_uint16(1200))
        if ret != 0:
            return None
        color = depth = df = None
        if frameready.color:
            ret, cf = cam.scGetFrame(ScFrameType.SC_COLOR_FRAME)
            if ret == 0:
                color = extract_color(cf)
        if frameready.depth:
            ret, dfr = cam.scGetFrame(ScFrameType.SC_DEPTH_FRAME)
            if ret == 0:
                depth = extract_depth(dfr)
                df = dfr
        if color is None or depth is None:
            return None
        return color, depth, df

    def save_capture(vdir, color, depth, df):
        vdir.mkdir(parents=True, exist_ok=True)
        # ファイル単体でどの組み合わせの撮影か分かるよう、ディレクトリ名と同じ接頭辞を
        # ファイル名にも付ける（<dirname>_color.png 等）。1つのフォルダに全部コピーして
        # 見比べる場合でも名前だけで区別できるようにするため。
        tag = vdir.name
        # 微妙な画質差も見比べたいため非可逆圧縮のjpgではなくpngで保存する
        cv2.imwrite(str(vdir / result_filename(tag, 'color', 'png')), color)
        cv2.imwrite(str(vdir / result_filename(tag, 'depth', 'png')), depth)
        depth_cm = make_depth_colormap(depth, depth_alpha)
        cv2.imwrite(str(vdir / result_filename(tag, 'depth_colormap', 'png')), depth_cm)
        if pointcloud:
            ret, pointlist = cam.scConvertDepthFrameToPointCloudVector(df)
            if ret == 0:
                save_ply(str(vdir / result_filename(tag, 'pointcloud', 'ply')), pointlist, df.width * df.height)
        return depth_cm

    def capture_auto_reference():
        """ToF・Color両方をAuto露光にして1枚だけ参考撮影する（比較の基準用）。
        撮影後は変更前のexposure control mode/exposure timeに戻す
        （このあとのスイープ本体に影響を与えないため）。
        """
        prev_tof_mode = cam.scGetExposureControlMode(ScSensorType.SC_TOF_SENSOR)[1]
        prev_tof_exp = cam.scGetExposureTime(ScSensorType.SC_TOF_SENSOR)[1]
        prev_color_mode = cam.scGetExposureControlMode(ScSensorType.SC_COLOR_SENSOR)[1]
        prev_color_exp = cam.scGetExposureTime(ScSensorType.SC_COLOR_SENSOR)[1]

        _check("scSetExposureControlMode(ToF->Auto)", cam.scSetExposureControlMode(
            ScSensorType.SC_TOF_SENSOR, ScExposureControlMode.SC_EXPOSURE_CONTROL_MODE_AUTO))
        _check("scSetExposureControlMode(Color->Auto)", cam.scSetExposureControlMode(
            ScSensorType.SC_COLOR_SENSOR, ScExposureControlMode.SC_EXPOSURE_CONTROL_MODE_AUTO))

        attempts = 0
        warm = 0
        while warm < warmup_frames and attempts < warmup_frames * 5:
            if grab_frame() is not None:
                warm += 1
            attempts += 1

        dirname = 'auto_reference'
        vdir = session.dir / dirname
        frame = grab_frame()
        saved = frame is not None
        actual = {
            'tof_exposure': cam.scGetExposureTime(ScSensorType.SC_TOF_SENSOR)[1],
            'color_exposure': cam.scGetExposureTime(ScSensorType.SC_COLOR_SENSOR)[1],
        }
        if saved:
            color, depth, df = frame
            save_capture(vdir, color, depth, df)
            print(f"  Auto参考撮影: 実機 tof_exposure={actual['tof_exposure']}us "
                  f"color_exposure={actual['color_exposure']}us  → {dirname}/")
        else:
            print("  警告: Auto参考撮影のフレーム取得に失敗しました")

        # 元の状態に戻す（Manualだった場合は露光時間も戻す）
        _check("scSetExposureControlMode(ToF restore)", cam.scSetExposureControlMode(
            ScSensorType.SC_TOF_SENSOR, ScExposureControlMode(prev_tof_mode)))
        if prev_tof_mode == ScExposureControlMode.SC_EXPOSURE_CONTROL_MODE_MANUAL.value:
            _check("scSetExposureTime(ToF restore)",
                   cam.scSetExposureTime(ScSensorType.SC_TOF_SENSOR, c_int32(prev_tof_exp)))
        _check("scSetExposureControlMode(Color restore)", cam.scSetExposureControlMode(
            ScSensorType.SC_COLOR_SENSOR, ScExposureControlMode(prev_color_mode)))
        if prev_color_mode == ScExposureControlMode.SC_EXPOSURE_CONTROL_MODE_MANUAL.value:
            _check("scSetExposureTime(Color restore)",
                   cam.scSetExposureTime(ScSensorType.SC_COLOR_SENSOR, c_int32(prev_color_exp)))

        return {'dirname': dirname, 'actual': actual, 'saved': saved}

    results = []
    aborted = False
    auto_reference_result = None

    try:
        if auto_reference:
            auto_reference_result = capture_auto_reference()

        for combo in combos:
            combo_casted = {name: apply_param(cam, name, value) for name, value in combo.items()}

            attempts = 0
            warm = 0
            while warm < warmup_frames and attempts < warmup_frames * 5:
                if grab_frame() is not None:
                    warm += 1
                attempts += 1

            actual = {name: param_specs[name]['getter'](cam) for name in combo_casted}
            match = all(str(combo_casted[n]) == str(actual[n]) for n in combo_casted)
            vdir = session.dir / combo_dirname(combo_casted)
            label = combo_label(combo_casted, unit_map)
            actual_label = combo_label(actual, unit_map)

            if mode == 'manual':
                saved = False
                skipped = False
                while not saved and not skipped:
                    frame = grab_frame()
                    if frame is None:
                        continue
                    color, depth, df = frame
                    preview = cv2.resize(color, (800, 600))
                    text = f"{label}  (実機: {actual_label})  [s]保存 [n]スキップ [q]終了"
                    cv2.putText(preview, text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
                    cv2.imshow('param_tune', preview)
                    key = cv2.waitKey(1) & 0xFF
                    if key == ord('s'):
                        save_capture(vdir, color, depth, df)
                        saved = True
                    elif key == ord('n'):
                        skipped = True
                    elif key == ord('q'):
                        aborted = True
                        skipped = True
                results.append({'combo': combo_casted, 'actual': actual, 'saved': saved, 'dirname': vdir.name,
                                 'fps': fps_state['fps']})
                print(f"  {'OK ' if match else '!! '}{label}  実機: {actual_label}  "
                      f"{'保存' if saved else 'スキップ'}")
                if aborted:
                    break
            else:
                frame = grab_frame()
                if frame is None:
                    print(f"  警告: フレーム取得に失敗、{label} をスキップします")
                    results.append({'combo': combo_casted, 'actual': actual, 'saved': False, 'dirname': vdir.name,
                                     'fps': fps_state['fps']})
                    continue
                color, depth, df = frame
                save_capture(vdir, color, depth, df)
                results.append({'combo': combo_casted, 'actual': actual, 'saved': True, 'dirname': vdir.name,
                                 'fps': fps_state['fps']})
                print(f"  {'OK ' if match else '!! '}{label}  実機: {actual_label}  → {vdir.name}/")

                preview = cv2.resize(color, (800, 600))
                cv2.putText(preview, f"{label} (実機: {actual_label})",
                            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
                cv2.imshow('param_tune', preview)
                key = cv2.waitKey(max(int(pause * 1000), 1)) & 0xFF
                if key == ord('q'):
                    aborted = True
                    break

    finally:
        sweeps_meta = [{'param': s['param'], 'label': param_specs[s['param']]['label'],
                        'unit': param_specs[s['param']]['unit'], 'values': s['values']}
                       for s in sweep_specs]
        session.write_metadata(
            camera={'model': 'NYX660',
                    'resolution': [cfg['camera'].get('color_width'), cfg['camera'].get('color_height')],
                    'fps': cfg['camera'].get('fps'),
                    'fps_final': fps_state['fps'],
                    'params_json': cfg['camera'].get('params_json')},
            params=names,
            sweeps=sweeps_meta,
            combine=combine,
            mode=mode,
            warmup_frames=warmup_frames,
            pointcloud=pointcloud,
            baseline_overrides=baseline_overrides,
            auto_fps_adjust=auto_fps_adjust,
            config_file=args.config,
            auto_reference=auto_reference_result,
            aborted=aborted,
            results=results,
        )
        close_camera(cam)
        cv2.destroyAllWindows()
        n_saved = sum(1 for r in results if r['saved'])
        print(f"\n完了: {n_saved}/{len(combos)} 通りを保存 → {session.dir}")
        if fps_state['fps'] != cfg['camera'].get('fps'):
            print(f"注意: FPS自動調整により、最終的なFPSは{cfg['camera'].get('fps')}から"
                  f"{fps_state['fps']}まで下がっています（metadata.jsonのcamera.fps_finalに記録）")
        if n_saved > 0:
            _build_comparison_images(session.dir, results, names, combine, unit_map, auto_reference_result)
        print(f"検出集計は次のコマンドで実行できます:")
        print(f"  python3 {Path(__file__).parent / 'detect_eval.py'} {session.dir}")
        gc.collect()


def _build_comparison_images(session_dir, results, names, combine, unit_map, auto_reference_result=None):
    """全組み合わせのcolor/depth_colormapを1枚のコンタクトシート画像にまとめて保存する。
    Auto参考撮影があり、1行グリッド（パラメータ1個）または自動配置の場合は先頭に含める
    （パラメータ2個の2次元グリッドには軸が壊れるため含めない。auto_reference/フォルダを案内する）。
    """
    saved = [r for r in results if r['saved']]
    if not saved:
        return
    grid_shape, ordered, axis_info = montage_grid_for_combos(saved, names, combine)
    ordered_results = ordered if ordered is not None else saved

    # パラメータ2個・直積の2次元グリッドの場合のみ、行/列見出し（値の昇順）を用意する。
    # どこからどこまでが同じ行・同じ列かを一目で分かるようにするため（境目の視認性向上）。
    row_labels = col_labels = row_axis_name = col_axis_name = None
    if axis_info:
        col_labels = [f"{v}{unit_map.get(axis_info['x_name'], '')}" for v in axis_info['x_values']]
        row_labels = [f"{v}{unit_map.get(axis_info['y_name'], '')}" for v in axis_info['y_values']]
        col_axis_name = axis_info['x_name']
        row_axis_name = axis_info['y_name']

    ref_entry = None
    if auto_reference_result and auto_reference_result.get('saved'):
        ref_entry = {'dirname': auto_reference_result['dirname'], 'combo': {}, '_is_reference': True}
    ordered_results, grid_shape, prepended = with_reference_tile(ordered_results, grid_shape, ref_entry)

    for modality in ('color', 'depth_colormap'):
        tiles = []
        for r in ordered_results:
            if r is None:
                tiles.append((None, ''))
                continue
            img_path = session_dir / r['dirname'] / result_filename(r['dirname'], modality, 'png')
            label = 'AUTO reference' if r.get('_is_reference') else combo_label(r['combo'], unit_map).replace(', ', '\n')
            tiles.append((str(img_path) if img_path.exists() else None, label))

        out_path = session_dir / f'comparison_{modality}.png'
        # cv2.putTextはASCIIのみ描画可能（日本語は文字化けする）ため英語表記にする
        title = f"{modality} comparison - {', '.join(names)}"
        if build_montage(tiles, out_path, title=title, grid_shape=grid_shape,
                          row_labels=row_labels, col_labels=col_labels,
                          row_axis_name=row_axis_name, col_axis_name=col_axis_name):
            print(f"比較画像: {out_path}")

    if ref_entry and not prepended:
        print(f"参考: Auto露光の撮影データは auto_reference/ に保存されています"
              f"（{len(names)}パラメータの比較グリッドには軸が合わないため含めていません）")


if __name__ == '__main__':
    main()
