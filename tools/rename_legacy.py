#!/usr/bin/env python3
"""旧命名で取得済みのデータを、新しい命名規則にそろえて出力する変換ツール。

新命名規則:
    {cam}_{YYMMDD}_{HHMMSS}_{NNNNN}_{mod}.{ext}
    例) nyx_260624_101741_00042_c.jpg

原データは変更せず、既定ではコピーで出力する（--move で移動）。
既定は dry-run で、実際に書き込むには --apply を付ける。

GUI版（フォルダを選んで、変換後の名前を確認してから実行する）:
    python3 tools/rename_legacy_gui.py

使い方:
    # 何が起きるか確認（dry-run）
    python3 tools/rename_legacy.py data/images

    # 実行（コピー）
    python3 tools/rename_legacy.py data/images --apply

    # セッションディレクトリを1つだけ変換
    python3 tools/rename_legacy.py data/images/2026_0624/image1_2026-06-24_101741_NYX660 --apply

    # ディレクトリ名から日時・カメラが判別できないデータ（click_test_data, mp4 など）
    python3 tools/rename_legacy.py data/click_test_data/250911_testdata_click \\
        --cam nyx --apply

対応している旧構造:
    images/<YYYY_MMDD>/image<N>_<YYYY-MM-DD>_<HHMMSS>_NYX660/<modality>/<...>.jpg
    timelapse_data/<YYYY_MMDD>/timelapse<N>_..._<MODEL>/<modality>/<...>.jpg
    pointcloud/<YYYY_MMDD>/pc<N>_..._<MODEL>/<...>.ply
    その他のフラットなディレクトリ（--cam を明示すれば変換可能）

同一ショットの color/depth/ir には同じ連番が振られる（旧ファイル名の
モダリティ接尾辞を除いた部分をキーにグループ化する）。labels/*.txt や
labelImg の *.xml も画像と同じ連番で追従する。
"""

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# 新命名のモダリティコード（収集スクリプトの utils.MOD_CODES と一致させること）
MOD_CODES = {
    'color':          'c',
    'depth':          'd',
    'depth_colormap': 'dc',
    'ir':             'i1',
    'ir_left':        'i1',
    'ir_right':       'i2',
    'ir_left_color':  'i1c',
    'ir_right_color': 'i2c',
    'pointcloud':     'pc',
    'points':         'pt',
    'detected':       'det',
    'annotated':      'det',
}

# 旧ファイル名の接尾辞 → モダリティ（長いものから順に判定する）
_SUFFIX_MAP = [
    ('_depth_colormap', 'depth_colormap'),
    ('_ir_left_color',  'ir_left_color'),
    ('_ir_right_color', 'ir_right_color'),
    ('_ir_left',        'ir_left'),
    ('_ir_right',       'ir_right'),
    ('_pointcloud',     'pointcloud'),
    ('_annotated',      'annotated'),
    ('_detected',       'detected'),
    ('_color',          'color'),
    ('_depth',          'depth'),
    ('_ir',             'ir'),
]

# 旧ファイル名の接頭辞 → モダリティ（click_test_data 形式）
_PREFIX_MAP = [
    ('image_',  'color'),
    ('points_', 'points'),
]

# 連番を振り直す対象の拡張子（それ以外は元の名前のままコピーする）
DATA_EXTS = {'.jpg', '.jpeg', '.png', '.ply', '.txt', '.xml', '.bag', '.mp4'}

# 旧セッションディレクトリ名  例: image1_2026-06-24_101741_D435
SESSION_RE = re.compile(
    r'^(?P<kind>[A-Za-z]+)(?P<n>\d*)_'
    r'(?P<date>\d{4}-\d{2}-\d{2})_(?P<time>\d{6})_(?P<model>[A-Za-z0-9]+)$'
)

# カメラモデル名 → カメラコード
_CAM_CODES = {'D435': 'd435', 'D405': 'd405', 'NYX660': 'nyx'}

# 変換済み（新命名）のファイル。二重変換を避けるため対象から除く
CONVERTED_RE = re.compile(r'^[A-Za-z0-9]+_\d{6}_\d{6}_\d{5}_[A-Za-z0-9]+$')


def cam_code_from_model(model):
    return _CAM_CODES.get(model.upper(), model.lower())


def split_modality(stem, fallback):
    """旧ファイル名の stem を (グループキー, モダリティ) に分解する。

    同一ショットの color/depth/ir は同じグループキーになる。
    判別できない場合は fallback（サブディレクトリ名）をモダリティとして使う。
    """
    for suf, mod in _SUFFIX_MAP:
        if stem.endswith(suf):
            return stem[:-len(suf)], mod
    for pre, mod in _PREFIX_MAP:
        if stem.startswith(pre):
            return stem[len(pre):], mod
    return stem, fallback


def group_sort_key(key):
    """グループキーを数値部分で自然順ソートするためのキー。"""
    parts = re.split(r'(\d+)', key)
    return [(0, int(p), '') if p.isdigit() else (1, 0, p) for p in parts]


def parse_session_dir(session_dir):
    """セッションディレクトリ名から (カメラコード, 開始日時) を取り出す。
    形式に合わなければ (None, None) を返す。
    """
    m = SESSION_RE.match(session_dir.name)
    if not m:
        return None, None
    try:
        started_at = datetime.strptime(f"{m['date']}_{m['time']}", '%Y-%m-%d_%H%M%S')
    except ValueError:
        return None, None
    return cam_code_from_model(m['model']), started_at


def oldest_mtime(session_dir):
    """ディレクトリ内で最も古いファイルの更新時刻（開始時刻の代用）。"""
    files = [p for p in session_dir.rglob('*') if p.is_file()]
    if not files:
        return None
    return datetime.fromtimestamp(min(p.stat().st_mtime for p in files))


def find_sessions(root):
    """変換対象のセッションディレクトリを探す。

    root 自身がセッション形式ならそれ1つ。そうでなければ配下を再帰的に探し、
    セッション形式のディレクトリを集める。1つも見つからなければ root 自身を
    （--cam 指定前提の）単一セッションとして扱う。
    """
    if parse_session_dir(root)[0] is not None:
        return [root]
    sessions = sorted(d for d in root.rglob('*')
                      if d.is_dir() and parse_session_dir(d)[0] is not None)
    return sessions if sessions else [root]


def plan_session(session_dir, out_root, cam, tag, started_at):
    """1セッション分の (コピー元, コピー先) のリストと prefix を作る。"""
    prefix   = f"{cam}_{started_at:%y%m%d}_{started_at:%H%M%S}"
    dir_name = f"{prefix}_{tag}" if tag else prefix
    dst_dir  = out_root / f"{started_at:%y%m%d}" / dir_name

    groups   = {}
    plain    = []   # 連番を振らずそのままコピーするファイル（json/csv など）
    for p in sorted(session_dir.rglob('*')):
        if not p.is_file():
            continue
        if CONVERTED_RE.match(p.stem):
            continue        # 変換済みのファイル（再実行時）
        sub = p.parent.relative_to(session_dir)
        if p.suffix.lower() not in DATA_EXTS:
            plain.append((p, dst_dir / sub / p.name))
            continue
        fallback = sub.name if sub.name else 'color'
        key, mod = split_modality(p.stem, fallback)
        groups.setdefault(key, []).append((p, mod, sub))

    pairs = []
    for idx, key in enumerate(sorted(groups, key=group_sort_key), start=1):
        for src, mod, sub in groups[key]:
            code = MOD_CODES.get(mod, mod)
            dst  = dst_dir / sub / f"{prefix}_{idx:05d}_{code}{src.suffix.lower()}"
            pairs.append((src, dst))
    pairs.extend(plain)
    return prefix, dst_dir, pairs


def count_shots(pairs):
    """変換後ファイル名からショット数（ユニークな連番の数）を数える。"""
    shots = set()
    for _, dst in pairs:
        m = re.search(r'_(\d{5})_[A-Za-z0-9]+$', dst.stem)
        if m:
            shots.add(m.group(1))
    return len(shots)


@dataclass
class Plan:
    """1セッション分の変換計画。CLI からも GUI からも同じものを使う。"""
    session_dir: Path
    dst_dir:     Path
    prefix:      str
    cam:         str
    started_at:  datetime
    pairs:       list = field(default_factory=list)   # [(src, dst), ...]
    shots:       int = 0
    note:        str = ''                             # 補足・警告


def build_plans(src_root, out=None, cam=None, tag=None):
    """変換元から変換計画を組み立てる。

    Returns:
        (plans, skipped)  skipped は [(ディレクトリ, 理由), ...]
    """
    src_root = Path(src_root).expanduser().resolve()
    plans, skipped = [], []

    for session_dir in find_sessions(src_root):
        dir_cam, started_at = parse_session_dir(session_dir)
        session_cam = cam or dir_cam
        note = ''

        if session_cam is None:
            skipped.append((session_dir, 'カメラを判別できません（カメラコードを指定してください）'))
            continue

        if started_at is None:
            started_at = oldest_mtime(session_dir)
            if started_at is None:
                skipped.append((session_dir, 'ファイルがありません'))
                continue
            note = ('ディレクトリ名から日時を取得できないため、'
                    f'最古ファイルの更新時刻を使用: {started_at:%Y-%m-%d %H:%M:%S}')

        # 出力ルート
        #   セッション形式（<種別>/<日付>/<セッション>）: 種別ルート直下に新日付で並べる
        #   それ以外                                    : 変換元ディレクトリの中に出す
        #                                                 （旧データと混ざらないように）
        if out:
            out_root = Path(out).expanduser().resolve()
        elif dir_cam is not None:
            out_root = session_dir.parent.parent
        else:
            out_root = session_dir

        prefix, dst_dir, pairs = plan_session(session_dir, out_root, session_cam,
                                              tag, started_at)
        if not pairs:
            skipped.append((session_dir, '対象ファイルなし'))
            continue

        plans.append(Plan(session_dir, dst_dir, prefix, session_cam, started_at,
                          pairs, count_shots(pairs), note))

    return plans, skipped


def apply_plan(plan, move=False, tag=None, on_file=None):
    """変換計画を実行する。

    Args:
        on_file: 1ファイルごとに呼ばれる callback(src, dst, status)。
                 status は 'ok' / 'exists' のいずれか。
    Returns:
        (書き出した数, 既存のためスキップした数)
    """
    written = skipped = 0
    for src, dst in plan.pairs:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            skipped += 1
            status = 'exists'
        else:
            if move:
                shutil.move(str(src), str(dst))
            else:
                shutil.copy2(src, dst)
            written += 1
            status = 'ok'
        if on_file:
            on_file(src, dst, status)

    write_metadata(plan.dst_dir, plan.prefix, plan.cam, tag,
                   plan.started_at, plan.session_dir, plan.shots)
    return written, skipped


def write_metadata(dst_dir, prefix, cam, tag, started_at, source, shot_count):
    """変換先に metadata.json を書く（既存があれば内容を残してマージする）。"""
    path = dst_dir / 'metadata.json'
    meta = {}
    if path.exists():
        try:
            with open(path) as f:
                meta = json.load(f)
        except (OSError, json.JSONDecodeError):
            meta = {}
    meta.update({
        'session_id':  prefix,
        'camera_code': cam,
        'started_at':  started_at.isoformat(timespec='seconds'),
        'tag':         tag,
        'naming':      '{cam}_{YYMMDD}_{HHMMSS}_{NNNNN}_{mod}.{ext}',
        'converted_from': str(source),
        'shot_count':  shot_count,
    })
    with open(path, 'w') as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(
        description='旧命名のデータを新命名規則にそろえて出力する',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='既定は dry-run。実際に書き込むには --apply を付けること。',
    )
    parser.add_argument('path', help='変換元（データルート / 日付ディレクトリ / セッションディレクトリ）')
    parser.add_argument('-o', '--out', default=None, metavar='DIR',
                        help='出力先ルート（既定: 日付ディレクトリの親＝変換元と同じ場所）')
    parser.add_argument('--cam', default=None, metavar='CODE',
                        help='カメラコード（d435 / d405 / nyx）。'
                             'ディレクトリ名から判別できない場合に必須')
    parser.add_argument('--tag', default=None, metavar='NAME',
                        help='出力セッションディレクトリ名に付けるタグ')
    parser.add_argument('--move', action='store_true',
                        help='コピーではなく移動する（原データが残らないので注意）')
    parser.add_argument('--apply', action='store_true',
                        help='実際にファイルを書き出す（省略時は表示のみ）')
    args = parser.parse_args()

    src_root = Path(args.path).expanduser().resolve()
    if not src_root.is_dir():
        print(f"ディレクトリが見つかりません: {src_root}")
        sys.exit(1)

    plans, skipped = build_plans(src_root, out=args.out, cam=args.cam, tag=args.tag)
    print(f"変換対象セッション: {len(plans)} 件\n")

    for session_dir, reason in skipped:
        print(f"[スキップ] {reason}: {session_dir}")
    if skipped:
        print()

    action = '移動' if args.move else 'コピー'
    total_files = 0
    for plan in plans:
        if plan.note:
            print(f"[情報] {plan.note}")
        print(f"{plan.session_dir}\n"
              f"  → {plan.dst_dir}  "
              f"({len(plan.pairs)} ファイル / {plan.shots} ショット, {action})")
        for src, dst in plan.pairs[:3]:
            print(f"      {src.name}  →  {dst.name}")
        if len(plan.pairs) > 3:
            print(f"      ... 他 {len(plan.pairs) - 3} 件")

        if args.apply:
            def _report(src, dst, status):
                if status == 'exists':
                    print(f"      [警告] 既に存在するためスキップ: {dst}")
            apply_plan(plan, move=args.move, tag=args.tag, on_file=_report)
        total_files += len(plan.pairs)
        print()

    print(f"{'完了' if args.apply else 'dry-run'}: "
          f"{total_files} ファイル / スキップ {len(skipped)} セッション")
    if not args.apply:
        print("実際に書き出すには --apply を付けて再実行してください。")
        print("GUI で選びながら変換する場合は tools/rename_legacy_gui.py を使ってください。")


if __name__ == '__main__':
    main()
