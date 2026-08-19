"""param_tune 配下のスクリプト（param_sweep.py / param_sweep_gui.py / detect_eval.py）が
共有するパラメータ定義・ユーティリティ。SDK非依存（GUI・集計スクリプトはカメラ未接続でも
このモジュールをimportできる）。

PARAM_META がスイープ可能なパラメータ名の単一の情報源。setter/getter（SDK個別API呼び出し）は
SDK初期化後でないと構築できないため param_sweep.py 側で組み立てる。

このモジュールに無いがSDKに存在するもの（意図的に対象外）:
  ExposureTimeOfHDR/WDR（HDR/WDRの各フレームごとの露光時間） — フレーム番号ごとに
    値を持つ配列パラメータでスカラー値のスイープに馴染まないため。ON/OFF自体は
    hdr_mode/wdr_modeとして対象に含めている（既定/直前設定の露光時間のまま使う）
  ColorAECROI — 値が4つ組でスカラー値のスイープに馴染まないため
  ColorResolution/ToFResolution — 解像度が変わると出力の形が変わり比較にならないため
    （config.yaml/--color-size で別途設定する運用のまま）
  ColorPixelFormat・DHCP/IP/SubnetMask・SoftwareTrigger・HWTrigger・
  TransformColorImgToDepthSensorEnabled等（アライン） — 画質パラメータではないため
    （アラインは dataset_point_collect.py --align が別途担当）
  FrameRate（FPS） — スイープ対象ではなく撮影条件（mode/warmup_frames等と同じ枠）
    として扱う。param_sweep_gui.py の基準FPSプルダウン・FPS自動調整トグル、
    param_sweep.py の --fps / --auto-fps-adjust を参照
    （露光上限がFPS依存のため。詳細はtof_exposure/color_exposureのhint参照）
"""

import json
import re
from pathlib import Path

import cv2
import numpy as np

# name -> ScepterGUITool由来のJSON（camera_params/*.json）上の位置・ラベル・値の型。
# json_section/json_key が None の項目はプロファイルJSONに現れない
# （ROS側で「device設定を維持」扱いのため。CLAUDE.md参照）。
#
# hint: SDK/実機調査で分かった範囲・目安をGUI/CLIに表示するための短い説明文。
# ScepterSDKのヘッダ・サンプルの多くは「Different products have different maximum
# value. Please refer to the product specification.」としか書いておらず、具体的な
# 数値範囲が非公開の項目が大半（color_gain・ir_gmm_gain・フィルタ閾値類など）。
# そのため「SDKで確認できた事実」と「このプロジェクトの現行プロファイル既定値」を
# 分けて書き、不確かな推測には「推定」「未確認」と明記している
# （2026-08-18 SDKサンプル/ヘッダ調査による。詳細はComma区切りで簡潔に）。
PARAM_META = {
    'tof_exposure': {
        'label': 'ToF露光時間', 'unit': 'us', 'type': 'int',
        'json_section': 'ExposureTime', 'json_key': 'ToF_ExposureTime',
        'hint': 'SDK下限:約58us / 上限:FPS依存で動的に決まる(SDK非公開の固定値ではない。'
                '目安: FPS30で約3万us、長い露光が要るなら--fpsを下げる) / プロファイル既定:3000us '
                '/ 上限超え要求は-105(SC_CMD_SYNC_TIME_OUT)で失敗し前の値のままクランプされる(実機確認済み)',
    },
    'color_exposure': {
        'label': 'Color露光時間', 'unit': 'us', 'type': 'int',
        'json_section': 'ExposureTime', 'json_key': 'Color_ExposureTime',
        'hint': 'SDK下限:100us / 上限:FPS依存で動的に決まる(SDK非公開の固定値ではない。'
                '目安: FPS30で約3.2万us、長い露光が要るなら--fpsを下げる) / プロファイル既定:3000us(通常はAuto運用) '
                '/ 上限超え要求は-105(SC_CMD_SYNC_TIME_OUT)で失敗し前の値のままクランプされる(実機確認済み。'
                '例:196000〜200000us要求→32000usのまま)',
    },
    'color_gain': {
        'label': 'Colorゲイン', 'unit': '', 'type': 'float',
        'json_section': None, 'json_key': None,
        'hint': 'SDKに数値範囲の記載なし(「製品仕様を参照」とのみ) / APIデフォルト値:1.0 / SDKサンプル例:3.5',
    },
    'color_aec_max_exposure_time': {
        'label': 'Color自動露光の上限', 'unit': 'us', 'type': 'int',
        'json_section': None, 'json_key': None,
        'hint': 'SDK下限:100us / 上限:FPS依存で動的に決まる(color_exposureと同じ制約) / SDKサンプル例:3000us',
    },
    'time_filter_threshold': {
        'label': '時間フィルタ閾値', 'unit': '', 'type': 'int',
        'json_section': 'Filter', 'json_key': 'TimeFilter_Threshold',
        'hint': 'SDKに数値範囲の記載なし / プロファイル既定:3',
    },
    'confidence_filter_threshold': {
        'label': '信頼度フィルタ閾値', 'unit': '', 'type': 'int',
        'json_section': 'Filter', 'json_key': 'ConfidenceFilter_Threshold',
        'hint': 'SDKに数値範囲の記載なし(0〜100のスケールと推定・未確認) / プロファイル既定:80',
    },
    'flying_pixel_filter_threshold': {
        'label': '飛び画素フィルタ閾値', 'unit': '', 'type': 'int',
        'json_section': 'Filter', 'json_key': 'FlyingPixelFilter_Threshold',
        'hint': 'SDKに数値範囲の記載なし / プロファイル既定:4',
    },
    'spatial_filter': {
        'label': '空間フィルタ（点群平滑化, 0=OFF/1=ON）', 'unit': '', 'type': 'bool',
        'json_section': 'Filter', 'json_key': 'SpatialFilter',
        'hint': '0=OFF/1=ONのみ / プロファイル既定:1(ON)',
    },
    'fillhole_filter': {
        'label': 'Fillholeフィルタ（点群穴埋め, 0=OFF/1=ON）', 'unit': '', 'type': 'bool',
        'json_section': 'Filter', 'json_key': 'Fillhole',
        'hint': '0=OFF/1=ONのみ / プロファイル既定:1(ON)',
    },
    'ir_gmm_gain': {
        'label': 'IR GMM Gain', 'unit': '', 'type': 'int',
        'json_section': 'Control', 'json_key': 'IRGmmGain',
        'hint': 'SDKに数値範囲の記載なし(c_uint8のため理論上0〜255) / APIデフォルト値:20 / SDKサンプル例:50',
    },
    'ir_gmm_correction_threshold': {
        'label': 'IR GMM補正閾値', 'unit': '', 'type': 'int',
        'json_section': 'Control', 'json_key': 'IRGmmCorrectionThreshold',
        'hint': 'SDKに数値範囲の記載なし / プロファイル既定:50',
    },
    'hdr_mode': {
        'label': 'HDRモード（複数露光合成, 0=OFF/1=ON）', 'unit': '', 'type': 'bool',
        'json_section': 'ExposureTime', 'json_key': 'HDR_Mode',
        'hint': '0=OFF/1=ONのみ / 各フレームの露光時間(ExposureTimeOfHDR)は本ツール未対応のため'
                'SDK既定・直前の設定のまま使われる / プロファイル既定:0(OFF)',
    },
    'wdr_mode': {
        'label': 'WDRモード（複数露光合成, 0=OFF/1=ON）', 'unit': '', 'type': 'bool',
        'json_section': 'ExposureTime', 'json_key': 'WDR_Mode',
        'hint': '0=OFF/1=ONのみ / 各フレームの露光時間(ExposureTimeOfWDR)は本ツール未対応のため'
                'SDK既定・直前の設定のまま使われる / プロファイル既定:0(OFF)',
    },
}


def cast_value(name, value):
    """PARAM_META[name]['type'] に従って value をPythonの型に変換する。"""
    t = PARAM_META[name]['type']
    if t == 'bool':
        if isinstance(value, str):
            v = value.strip().lower()
            if v in ('1', 'true', 'on', 'yes'):
                return True
            if v in ('0', 'false', 'off', 'no'):
                return False
        return bool(float(value))
    if t == 'float':
        return float(value)
    return int(round(float(value)))


def load_profile_baseline(params_json_path):
    """現行プロファイル（params_json）から PARAM_META 各項目の現在値を読み取る。
    値が存在しない項目（json_section未指定 or JSON側に無い）は結果に含めない。
    読み込み失敗時は空辞書を返す。
    """
    try:
        with open(params_json_path) as f:
            params = json.load(f)
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    baseline = {}
    for name, meta in PARAM_META.items():
        if not meta['json_section']:
            continue
        section = params.get(meta['json_section'], {})
        if meta['json_key'] in section:
            baseline[name] = section[meta['json_key']]
    return baseline


def parse_values(spec):
    """"1000,2000,3000" または "1:10:1"（start:stop:step, stop含む）を数値リストに変換する。"""
    spec = str(spec).strip()
    if ',' in spec:
        vals = [float(x) for x in spec.split(',')]
    else:
        parts = spec.split(':')
        if len(parts) == 2:
            start, stop = map(float, parts)
            step = 1.0
        elif len(parts) == 3:
            start, stop, step = map(float, parts)
        else:
            raise ValueError(f"値の形式が不正です: {spec!r}（例: 1000,2000 または 1:10:1）")
        if step == 0:
            raise ValueError("step に0は指定できません")
        n = int(round((stop - start) / step)) + 1
        vals = [start + i * step for i in range(max(n, 0))]
    if all(float(v).is_integer() for v in vals):
        vals = [int(v) for v in vals]
    return vals


def safe_dirname(value):
    """値をディレクトリ名として安全な文字列に変換する（負数・小数対応）。"""
    s = str(value)
    return re.sub(r'[^0-9A-Za-z]', lambda m: {'.': 'p', '-': 'neg'}.get(m.group(), '_'), s)


def combo_dirname(combo):
    """複数パラメータの値の組み合わせ(dict)をディレクトリ名に変換する。
    例: {'tof_exposure': 3000, 'time_filter_threshold': 3} -> 'tof_exposure-3000_time_filter_threshold-3'
    """
    return "_".join(f"{name}-{safe_dirname(value)}" for name, value in combo.items())


def combo_label(combo, unit_map=None):
    """複数パラメータの値の組み合わせを人間が読める文字列にする（ログ・グラフラベル用）。"""
    unit_map = unit_map or {}
    return ", ".join(f"{name}={value}{unit_map.get(name, '')}" for name, value in combo.items())


def find_latest_session(param_tune_dir, param=None):
    """param_tune_dir配下から最新のsweepセッション（metadata.json持ち）ディレクトリを探す。
    param 指定時はそのパラメータを含むセッションに絞る。見つからなければ None。
    """
    base = Path(param_tune_dir).expanduser()
    metas = sorted(base.glob('*/*/metadata.json'))
    if param:
        metas = [m for m in metas if param in _metadata_params(m)]
    if not metas:
        return None
    return metas[-1].parent


def find_resumable_session(param_tune_dir, dir_tag):
    """param_tune_dir配下から、dir_tag（パラメータ名を"+"で繋いだもの。tag付きなら
    "<dir_tag>_<tag>"）に一致するセッションディレクトリのうち最後に更新されたものを返す。
    metadata.jsonの有無は問わない（充電切れ等でプロセスが強制終了しmetadata.jsonが
    書けなかったセッションも再開対象にするため）。見つからなければ None。
    """
    base = Path(param_tune_dir).expanduser()
    candidates = [d for d in base.glob(f'*/*_{dir_tag}') if d.is_dir()]
    candidates += [d for d in base.glob(f'*/*_{dir_tag}_*') if d.is_dir()]
    if not candidates:
        return None
    return max(set(candidates), key=lambda d: d.stat().st_mtime)


def session_has_result(vdir, dirname, pointcloud=False, ir=False):
    """組み合わせディレクトリ vdir に、そのセッションで期待される撮影結果一式
    （color/depth/depth_colormap、pointcloud指定時は.ply、ir指定時はir.pngも）が
    既に揃っているか判定する。再開時にどの組み合わせを撮り直さずスキップできるかの判定に使う。
    """
    if not vdir.is_dir():
        return False
    required = (['color', 'depth', 'depth_colormap'] + (['pointcloud'] if pointcloud else [])
                + (['ir'] if ir else []))
    exts = {'pointcloud': 'ply'}
    return all((vdir / result_filename(dirname, m, exts.get(m, 'png'))).exists() for m in required)


def list_sessions(param_tune_dir, limit=100):
    """param_tune_dir配下の全セッションディレクトリを更新日時の新しい順に返す
    （GUIのセッション選択欄向け）。metadata.jsonの有無・撮影進捗を短い注記として
    labelに含める（無ければ「中断の可能性」と分かるようにする）。
    戻り値: [{'path': Path, 'label': str}, ...]
    """
    base = Path(param_tune_dir).expanduser()
    if not base.is_dir():
        return []
    dirs = [d for d in base.glob('*/*') if d.is_dir()]
    dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)

    out = []
    for d in dirs[:limit]:
        meta_path = d / 'metadata.json'
        note = '  (metadata.jsonなし・中断の可能性)'
        if meta_path.exists():
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
                results = meta.get('results', [])
                n_saved = sum(1 for r in results if r.get('saved'))
                note = f"  ({n_saved}/{len(results)}枚保存済み" + (', 中断' if meta.get('aborted') else '') + ')'
            except (OSError, json.JSONDecodeError):
                note = '  (metadata.json破損)'
        out.append({'path': d, 'label': f"{d.parent.name}/{d.name}{note}"})
    return out


def _metadata_params(meta_path):
    """metadata.json からスイープ対象パラメータ名の一覧を取り出す
    （新形式の 'params'/'sweeps' と、単一パラメータだった旧形式の 'param' の両方に対応）。
    """
    try:
        with open(meta_path) as f:
            meta = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    if 'params' in meta:
        return meta['params']
    if 'sweeps' in meta:
        return [s['param'] for s in meta['sweeps']]
    if 'param' in meta:
        return [meta['param']]
    return []


def result_filename(dirname, modality, ext):
    """組み合わせディレクトリ内のファイル名（命名規則: <dirname>_<modality>.<ext>）を返す。"""
    return f"{dirname}_{modality}.{ext}"


def find_result_file(vdir, dirname, modality, ext):
    """<dirname>_<modality>.<ext> を探す。無ければ旧形式（<modality>.<ext> のみ）にも対応する。
    見つからなければ None。
    """
    for candidate in (vdir / result_filename(dirname, modality, ext), vdir / f"{modality}.{ext}"):
        if candidate.exists():
            return candidate
    return None


def _sort_key(v):
    """数値なら数値として、それ以外は文字列としてソートするためのキー。

    sorted(..., key=str) だと "1000" と "200" のように桁数で文字列比較されてしまい
    （"1000" < "200"）、比較画像（comparison_*.png）の並びが値の大小と一致せず
    「ずれて見える」問題があったため（2026-08-19）。bool は float に変換できる
    （True->1.0/False->0.0）ためそのまま数値として扱われる。
    """
    try:
        return (0, float(v))
    except (TypeError, ValueError):
        return (1, str(v))


def montage_grid_for_combos(results, names, combine=None, combo_key='combo'):
    """比較モンタージュ用のグリッド配置を決める。

    - パラメータ1個: 値の大小でソートした1行のグリッド（値が多すぎる場合はNoneであきらめて自動配置に委ねる）
    - パラメータ2個 かつ combine=='product': (行=2個目の値, 列=1個目の値) の2次元グリッド、
      各軸とも値の大小順に整列する（detect_eval.py のヒートマップと軸の意味を揃えている）
    - それ以外（3個以上 or zip）: None を返す（呼び出し側は結果の並び順のまま自動グリッドにする）

    combo_key: 各要素から組み合わせdictを取り出すキー名。param_sweep.py の results は
    'combo'、detect_eval.py の rows は内部フィールド名が異なる（既定'_combo'相当）ため
    呼び出し側で指定できるようにしている。

    戻り値: (grid_shape, ordered_results, axis_info)。
    ordered_results は results と同じ要素を並べ替えたリストで、対応する組み合わせが
    無いセルは None が入る。axis_info はパラメータ2個・直積の場合のみ
    {'x_name', 'x_values', 'y_name', 'y_values'}（行/列ヘッダー描画用、値は整列済み昇順）、
    それ以外は None。グリッドを組めない場合は (None, None, None)。
    """
    if len(names) == 1:
        name = names[0]
        ordered = sorted(results, key=lambda r: _sort_key(r[combo_key][name]))
        if len(ordered) <= 12:
            return (1, len(ordered)), ordered, None
        return None, None, None

    if len(names) == 2 and combine == 'product':
        name_x, name_y = names
        xs = sorted({r[combo_key][name_x] for r in results}, key=_sort_key)
        ys = sorted({r[combo_key][name_y] for r in results}, key=_sort_key)
        index = {(r[combo_key][name_x], r[combo_key][name_y]): r for r in results}
        ordered = [index.get((x, y)) for y in ys for x in xs]
        axis_info = {'x_name': name_x, 'x_values': xs, 'y_name': name_y, 'y_values': ys}
        return (len(ys), len(xs)), ordered, axis_info

    return None, None, None


def with_reference_tile(ordered_results, grid_shape, ref_entry):
    """Auto露光での参考撮影（ref_entry）をモンタージュの先頭に追加できるか判定し、
    追加できれば ordered_results/grid_shape に組み込んで返す。

    1行グリッド（パラメータ1個）または自動配置（grid_shape=None）の場合のみ、
    先頭に1枠追加して差し込める。2次元グリッド（パラメータ2個・直積）は
    行/列の意味が壊れるため追加しない（呼び出し側は別途 auto_reference/ フォルダを
    案内すること）。

    戻り値: (ordered_results, grid_shape, prepended: bool)
    """
    if ref_entry is None:
        return ordered_results, grid_shape, False
    if grid_shape is not None and grid_shape[0] != 1:
        return ordered_results, grid_shape, False
    new_ordered = [ref_entry] + list(ordered_results)
    new_grid_shape = (grid_shape[0], grid_shape[1] + 1) if grid_shape is not None else None
    return new_ordered, new_grid_shape, True


def build_montage(tiles, out_path, title=None, grid_shape=None, thumb_width=None,
                   row_labels=None, col_labels=None, row_axis_name=None, col_axis_name=None):
    """複数の画像を1枚のグリッド画像（コンタクトシート）にまとめて保存する。

    tiles: [(画像パス or ndarray or None, ラベル文字列), ...]（Noneは空セル）
    grid_shape: (rows, cols)。指定が無ければ枚数から正方形に近い形を自動算出する。

    row_labels/col_labels: 2次元グリッド（パラメータ2個・直積、grid_shape の行数・列数と
    それぞれ同じ個数）を渡すと、上端に列見出し・左端に行見出しの帯を追加で描画し、
    どこからどこまでが同じ行・同じ列（=同じパラメータ値）かを一目で分かるようにする
    （行数・列数どちらも2以上の場合のみ有効。個数が一致しない場合は無視する）。
    row_axis_name/col_axis_name はその見出し帯の左上隅に表示するパラメータ名。

    戻り値: 保存できれば True、有効な画像が1枚も無ければ False。
    """
    n = len(tiles)
    if n == 0:
        return False

    if thumb_width is None:
        thumb_width = 360 if n <= 12 else (280 if n <= 30 else 200)

    def _load(img_or_path):
        if img_or_path is None:
            return None
        if isinstance(img_or_path, (str, Path)):
            return cv2.imread(str(img_or_path))
        return img_or_path

    imgs = [(_load(im), label) for im, label in tiles]
    ref = next((im for im, _ in imgs if im is not None), None)
    if ref is None:
        return False
    thumb_height = int(thumb_width * ref.shape[0] / ref.shape[1])

    if grid_shape:
        rows, cols = grid_shape
    else:
        cols = int(np.ceil(np.sqrt(n)))
        rows = int(np.ceil(n / cols))

    lines_max = max((label.count('\n') + 1 for _, label in imgs if label), default=1)
    label_h = 16 * lines_max + 8
    tile_h = thumb_height + label_h
    tile_w = thumb_width
    title_h = 34 if title else 0

    # 行/列見出し帯（境目を分かりやすくするための軸ラベル）。行数・列数がともに2以上、
    # かつラベル数が実際の行数・列数と一致する場合のみ描く（1次元グリッドやラベル未指定時は無し）。
    has_axes = (rows > 1 and cols > 1
                and row_labels is not None and col_labels is not None
                and len(row_labels) == rows and len(col_labels) == cols)
    col_header_h = 30 if has_axes else 0
    row_header_w = 120 if has_axes else 0

    canvas = np.full((rows * tile_h + title_h + col_header_h, cols * tile_w + row_header_w, 3),
                      255, dtype=np.uint8)
    if title:
        cv2.putText(canvas, title, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (0, 0, 0), 1, cv2.LINE_AA)

    if has_axes:
        header_bg = (232, 232, 244)
        header_border = (150, 150, 175)
        # 列見出し（上端。各列＝col_axis_nameの値）
        for c, clabel in enumerate(col_labels):
            x0, y0 = row_header_w + c * tile_w, title_h
            cv2.rectangle(canvas, (x0, y0), (x0 + tile_w - 1, y0 + col_header_h - 1), header_bg, -1)
            cv2.putText(canvas, str(clabel), (x0 + 6, y0 + col_header_h - 9),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (20, 20, 20), 1, cv2.LINE_AA)
            cv2.rectangle(canvas, (x0, y0), (x0 + tile_w - 1, y0 + col_header_h - 1), header_border, 1)
        # 行見出し（左端。各行＝row_axis_nameの値）
        for r, rlabel in enumerate(row_labels):
            x0, y0 = 0, title_h + col_header_h + r * tile_h
            cv2.rectangle(canvas, (x0, y0), (x0 + row_header_w - 1, y0 + tile_h - 1), header_bg, -1)
            cv2.putText(canvas, str(rlabel), (x0 + 6, y0 + tile_h // 2 + 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (20, 20, 20), 1, cv2.LINE_AA)
            cv2.rectangle(canvas, (x0, y0), (x0 + row_header_w - 1, y0 + tile_h - 1), header_border, 1)
        # 左上隅（行/列がそれぞれ何のパラメータかを示す）
        corner_text = f"{row_axis_name or ''}\\{col_axis_name or ''}"
        cv2.rectangle(canvas, (0, title_h), (row_header_w - 1, title_h + col_header_h - 1),
                      (215, 215, 230), -1)
        cv2.putText(canvas, corner_text[:16], (4, title_h + col_header_h - 9),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (60, 60, 60), 1, cv2.LINE_AA)
        cv2.rectangle(canvas, (0, title_h), (row_header_w - 1, title_h + col_header_h - 1),
                      header_border, 1)

    for idx in range(min(n, rows * cols)):
        img, label = imgs[idx]
        r, c = divmod(idx, cols)
        y0 = title_h + col_header_h + r * tile_h
        x0 = row_header_w + c * tile_w

        cell = np.full((tile_h, tile_w, 3), 235, dtype=np.uint8)
        if img is not None:
            cell[0:thumb_height, 0:thumb_width] = cv2.resize(img, (thumb_width, thumb_height))
        else:
            cv2.putText(cell, '(no data)', (8, thumb_height // 2), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, (120, 120, 120), 1, cv2.LINE_AA)
        cv2.rectangle(cell, (0, thumb_height), (tile_w - 1, tile_h - 1), (248, 248, 248), -1)
        for li, line in enumerate((label or '').split('\n')):
            cv2.putText(cell, line, (4, thumb_height + 15 + 16 * li), cv2.FONT_HERSHEY_SIMPLEX,
                        0.42, (20, 20, 20), 1, cv2.LINE_AA)
        cv2.rectangle(cell, (0, 0), (tile_w - 1, tile_h - 1), (190, 190, 190), 1)

        canvas[y0:y0 + tile_h, x0:x0 + tile_w] = cell

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), canvas)
    return True
