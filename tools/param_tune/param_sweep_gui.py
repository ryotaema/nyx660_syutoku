#!/usr/bin/env python3
"""param_sweep.py の撮影設定をGUIで作成し、そのまま撮影を開始するツール。

- 振るパラメータを1つ、または「+パラメータを追加」で複数追加して同時に振れる
  （複数指定時は「組み合わせ方」で 直積=全組み合わせ / 対応=1対1 を選べる）
- 値の範囲（"1000,2000,3000" または "start:stop:step"）
- モード（auto/manual）・待機フレーム数・撮影後の表示秒数・点群保存の有無
- 振らない他のパラメータの基準値（既定は現行プロファイルの値、上書き可能）

をフォームで設定して JSON（tools/param_tune/configs/*.json）に保存し、
そのまま「保存して撮影開始」で param_sweep.py --config <path> をサブプロセスで
起動する（撮影中のプレビューウィンドウは別プロセスのcv2ウィンドウとして開く）。

使い方:
    python3 tools/param_tune/param_sweep_gui.py
"""

import sys
import json
import subprocess
import threading
import queue
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / 'nyx660_script'))

from common import PARAM_META, parse_values, load_profile_baseline
from utils import load_config

import tkinter as tk
from tkinter import ttk, messagebox

SWEEP_SCRIPT = Path(__file__).resolve().parent / 'param_sweep.py'
CONFIG_DIR   = Path(__file__).resolve().parent / 'configs'


def _display_for(name):
    meta = PARAM_META[name]
    unit = f"[{meta['unit']}]" if meta['unit'] else ''
    return f"{meta['label']}{unit} ({name})"


class ParamSweepGUI:
    def __init__(self, root):
        self.root = root
        root.title('NYX660 パラメータ撮影設定')
        root.geometry('920x820')
        root.minsize(700, 480)

        self.cfg = load_config()
        self.profile_path = self.cfg['camera'].get('params_json')
        self.profile_baseline = load_profile_baseline(self.profile_path) if self.profile_path else {}
        self.fps = int(self.cfg['camera'].get('fps', 30))

        # ユーザー編集を保持する作業コピー（プロファイル既定値で初期化）
        self.baseline_values = dict(self.profile_baseline)
        self.baseline_entries = {}  # name -> Entry（現在表示中のものだけ）
        self.sweep_rows = []        # [{'frame','param_var','values_var','preview_var',...}, ...]

        self.display_to_name = {_display_for(n): n for n in PARAM_META}
        self.name_to_display = {n: d for d, n in self.display_to_name.items()}

        self.proc = None
        self.log_queue = queue.Queue()

        self._build_widgets()
        self._add_sweep_row()
        self.root.after(100, self._poll_log_queue)

    # ------------------------------------------------------------------ UI

    def _build_widgets(self):
        # 振るパラメータ・基準値の行はヒント表示のぶん縦に伸びやすく、パラメータ数が
        # 増えるとウィンドウ高さを超えて操作できなくなるため、可変長の設定部分だけを
        # スクロール領域にする。実行ボタンとログは常にウィンドウ下部に固定表示する
        # （下から先にpackすることで、スクロール領域が残り空間を占めるようにしている）。
        btn_frame = ttk.Frame(self.root, padding=8)
        btn_frame.pack(side='bottom', fill='x')
        ttk.Button(btn_frame, text="設定を保存", command=lambda: self.on_save(start=False)).pack(side='left')
        self.start_button = ttk.Button(btn_frame, text="保存して撮影開始", command=lambda: self.on_save(start=True))
        self.start_button.pack(side='left', padx=(8, 0))
        self.abort_button = ttk.Button(btn_frame, text="強制終了", command=self._abort, state='disabled')
        self.abort_button.pack(side='left', padx=(8, 0))

        log_frame = ttk.LabelFrame(self.root, text="実行ログ", padding=4)
        log_frame.pack(side='bottom', fill='x', padx=8, pady=(0, 8))
        self.log_widget = tk.Text(log_frame, height=10, state='disabled', wrap='word')
        self.log_widget.pack(fill='both', expand=True, side='left')
        log_scroll = ttk.Scrollbar(log_frame, command=self.log_widget.yview)
        log_scroll.pack(fill='y', side='right')
        self.log_widget['yscrollcommand'] = log_scroll.set

        scroll_outer = ttk.Frame(self.root)
        scroll_outer.pack(side='top', fill='both', expand=True)
        canvas = tk.Canvas(scroll_outer, highlightthickness=0)
        form_scroll = ttk.Scrollbar(scroll_outer, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=form_scroll.set)
        canvas.pack(side='left', fill='both', expand=True)
        form_scroll.pack(side='right', fill='y')

        scroll_frame = ttk.Frame(canvas)
        canvas_window = canvas.create_window((0, 0), window=scroll_frame, anchor='nw')
        scroll_frame.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda e: canvas.itemconfig(canvas_window, width=e.width))

        def _wheel(event):
            delta = event.delta if event.delta else (-120 if event.num == 4 else 120)
            canvas.yview_scroll(int(-delta / 120), 'units')

        canvas.bind('<Enter>', lambda e: (canvas.bind_all('<MouseWheel>', _wheel),
                                           canvas.bind_all('<Button-4>', _wheel),
                                           canvas.bind_all('<Button-5>', _wheel)))
        canvas.bind('<Leave>', lambda e: (canvas.unbind_all('<MouseWheel>'),
                                           canvas.unbind_all('<Button-4>'),
                                           canvas.unbind_all('<Button-5>')))

        top = ttk.Frame(scroll_frame, padding=8)
        top.pack(fill='x')
        ttk.Label(top, text=f"プロファイル: {self.profile_path or '(未設定)'}").pack(anchor='w')
        ttk.Label(top, text=f"保存先: {self.cfg['output']['param_tune_dir']}").pack(anchor='w')

        # --- 振るパラメータ（複数行） ---
        sweeps_outer = ttk.LabelFrame(scroll_frame, text="振るパラメータ（複数追加すると同時に振れる）", padding=8)
        sweeps_outer.pack(fill='x', padx=8, pady=(4, 4))

        header = ttk.Frame(sweeps_outer)
        header.pack(fill='x')
        ttk.Label(header, text="パラメータ", width=32).pack(side='left')
        ttk.Label(header, text="値（1000,2000,3000 または 1:10:1）", width=30).pack(side='left', padx=(6, 0))

        self.sweeps_container = ttk.Frame(sweeps_outer)
        self.sweeps_container.pack(fill='x', pady=(2, 4))

        add_row = ttk.Frame(sweeps_outer)
        add_row.pack(fill='x')
        ttk.Button(add_row, text="+ パラメータを追加", command=self._add_sweep_row).pack(side='left')

        combine_row = ttk.Frame(sweeps_outer)
        combine_row.pack(fill='x', pady=(6, 0))
        ttk.Label(combine_row, text="組み合わせ方（2つ以上の時）").pack(side='left')
        self.combine_var = tk.StringVar(value='product')
        ttk.Radiobutton(combine_row, text='直積（全組み合わせ）', variable=self.combine_var, value='product',
                         command=self._update_combo_count).pack(side='left', padx=(8, 0))
        ttk.Radiobutton(combine_row, text='対応（1対1、値の個数を揃える）', variable=self.combine_var, value='zip',
                         command=self._update_combo_count).pack(side='left', padx=(8, 0))

        self.combo_count_var = tk.StringVar()
        ttk.Label(sweeps_outer, textvariable=self.combo_count_var, foreground='gray').pack(anchor='w', pady=(2, 0))

        # --- 撮影条件 ---
        form = ttk.Frame(scroll_frame, padding=8)
        form.pack(fill='x')
        form.columnconfigure(1, weight=1)
        row = 0

        ttk.Label(form, text="モード").grid(row=row, column=0, sticky='w', pady=3)
        mode_frame = ttk.Frame(form)
        mode_frame.grid(row=row, column=1, sticky='w')
        self.mode_var = tk.StringVar(value='auto')
        ttk.Radiobutton(mode_frame, text='auto（自動で撮影）', variable=self.mode_var, value='auto').pack(side='left')
        ttk.Radiobutton(mode_frame, text='manual（[s]で撮影・[n]でスキップ）',
                         variable=self.mode_var, value='manual').pack(side='left', padx=(12, 0))
        row += 1

        ttk.Label(form, text="基準FPS（露光時間の上限に直結）").grid(row=row, column=0, sticky='w', pady=3)
        fps_frame = ttk.Frame(form)
        fps_frame.grid(row=row, column=1, sticky='w')
        self.fps_var = tk.StringVar(value=str(self.fps if self.fps in (15, 30) else 30))
        fps_combo = ttk.Combobox(fps_frame, textvariable=self.fps_var, state='readonly',
                                  values=['15', '30'], width=4)
        fps_combo.pack(side='left')
        fps_combo.bind('<<ComboboxSelected>>', self._on_fps_changed)
        ttk.Label(fps_frame, text="（低いFPSほど露光を長く取れる）", foreground='gray')\
            .pack(side='left', padx=(8, 0))
        row += 1

        self.auto_fps_adjust_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(form, text="露光がFPS上限を超えたら自動でFPSを下げて撮影を続ける（FPS自動調整）",
                         variable=self.auto_fps_adjust_var).grid(row=row, column=1, sticky='w', pady=3)
        row += 1

        ttk.Label(form, text="warmupフレーム数（値変更後の安定待ち）").grid(row=row, column=0, sticky='w', pady=3)
        warm_frame = ttk.Frame(form)
        warm_frame.grid(row=row, column=1, sticky='w')
        self.warmup_var = tk.IntVar(value=10)
        ttk.Spinbox(warm_frame, from_=0, to=300, textvariable=self.warmup_var, width=6,
                    command=self._update_warmup_seconds).pack(side='left')
        self.warmup_var.trace_add('write', self._update_warmup_seconds)
        self.warmup_seconds_var = tk.StringVar()
        ttk.Label(warm_frame, textvariable=self.warmup_seconds_var, foreground='gray').pack(side='left', padx=(8, 0))
        row += 1
        self._update_warmup_seconds()

        ttk.Label(form, text="pause秒（autoモードで撮影結果を表示する時間）").grid(row=row, column=0, sticky='w', pady=3)
        self.pause_var = tk.DoubleVar(value=0.3)
        ttk.Spinbox(form, from_=0.0, to=10.0, increment=0.1, textvariable=self.pause_var, width=6)\
            .grid(row=row, column=1, sticky='w')
        row += 1

        self.pointcloud_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(form, text="点群(.ply)も保存する", variable=self.pointcloud_var)\
            .grid(row=row, column=1, sticky='w', pady=3)
        row += 1

        self.auto_reference_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(form, text="Auto露光の参考撮影を含める（ToF/Color両方Auto、最初に1回のみ）",
                         variable=self.auto_reference_var).grid(row=row, column=1, sticky='w', pady=3)
        row += 1

        ttk.Label(form, text="タグ（保存フォルダ名に付加、任意）").grid(row=row, column=0, sticky='w', pady=3)
        self.tag_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.tag_var, width=30).grid(row=row, column=1, sticky='w', pady=3)
        row += 1

        # --- 基準値（振らない他パラメータ） ---
        baseline_outer = ttk.LabelFrame(scroll_frame, text="基準値（振らないパラメータの固定値。空欄でSDK既定 = プロファイル値）",
                                         padding=8)
        baseline_outer.pack(fill='x', padx=8, pady=(4, 4))
        reload_row = ttk.Frame(baseline_outer)
        reload_row.pack(fill='x')
        ttk.Button(reload_row, text="プロファイルの値に戻す", command=self._reset_baseline_to_profile)\
            .pack(side='left')
        self.baseline_frame = ttk.Frame(baseline_outer)
        self.baseline_frame.pack(fill='x', pady=(6, 0))

    # ------------------------------------------------------- 振るパラメータ行

    def _used_names(self, exclude_index=None):
        used = set()
        for i, row in enumerate(self.sweep_rows):
            if i == exclude_index:
                continue
            used.add(self.display_to_name[row['param_var'].get()])
        return used

    def _next_available_name(self, exclude_index=None):
        used = self._used_names(exclude_index)
        for name in PARAM_META:
            if name not in used:
                return name
        return None

    def _add_sweep_row(self):
        default_name = self._next_available_name()
        if default_name is None:
            messagebox.showinfo('追加不可', '全パラメータが選択済みです。')
            return

        # 1行目: パラメータ選択・値入力・削除ボタン
        # 2行目: SDK範囲/推奨値のヒント・パース結果プレビュー
        # （横に並べると欄が広くなりすぎるため2段組みにしている）
        row_frame = ttk.Frame(self.sweeps_container)
        row_frame.pack(fill='x', pady=(2, 6))

        line1 = ttk.Frame(row_frame)
        line1.pack(fill='x')

        param_var = tk.StringVar(value=self.name_to_display[default_name])
        combo = ttk.Combobox(line1, textvariable=param_var, state='readonly', width=30)
        combo.pack(side='left')

        values_var = tk.StringVar()
        entry = ttk.Entry(line1, textvariable=values_var, width=26)
        entry.pack(side='left', padx=(6, 0))

        line2 = ttk.Frame(row_frame)
        line2.pack(fill='x')

        hint_var = tk.StringVar()
        ttk.Label(line2, textvariable=hint_var, foreground='#0a6', wraplength=760, justify='left')\
            .pack(side='left', anchor='w')

        preview_var = tk.StringVar()
        preview_label = ttk.Label(row_frame, textvariable=preview_var, foreground='gray')
        preview_label.pack(anchor='w')

        row = {'frame': row_frame, 'param_var': param_var, 'values_var': values_var,
               'preview_var': preview_var, 'preview_label': preview_label, 'combo': combo,
               'hint_var': hint_var}

        remove_btn = ttk.Button(line1, text='削除', width=4,
                                 command=lambda: self._remove_sweep_row(row))
        remove_btn.pack(side='left', padx=(6, 0))

        self.sweep_rows.append(row)

        combo.bind('<<ComboboxSelected>>', lambda e: self._on_sweep_rows_changed())
        entry.bind('<KeyRelease>', lambda e, r=row: (self._update_row_preview(r), self._update_combo_count()))

        self._on_sweep_rows_changed()

    def _remove_sweep_row(self, row):
        if len(self.sweep_rows) <= 1:
            messagebox.showinfo('削除不可', '少なくとも1つのパラメータが必要です。')
            return
        row['frame'].destroy()
        self.sweep_rows.remove(row)
        self._on_sweep_rows_changed()

    def _refresh_sweep_dropdowns(self):
        for i, row in enumerate(self.sweep_rows):
            used_by_others = self._used_names(exclude_index=i)
            choices = [self.name_to_display[n] for n in PARAM_META if n not in used_by_others]
            row['combo']['values'] = choices
            if row['param_var'].get() not in choices and choices:
                row['param_var'].set(choices[0])
            self._update_row_hint(row)

    def _update_row_hint(self, row):
        name = self.display_to_name[row['param_var'].get()]
        row['hint_var'].set(PARAM_META[name]['hint'])

    def _update_row_preview(self, row):
        spec = row['values_var'].get().strip()
        if not spec:
            row['preview_var'].set('')
            return
        try:
            vals = parse_values(spec)
            preview = ', '.join(str(v) for v in vals[:8])
            more = f" …他{len(vals) - 8}件" if len(vals) > 8 else ''
            row['preview_var'].set(f"→{len(vals)}件: {preview}{more}")
            row['preview_label'].configure(foreground='gray')
        except Exception as e:
            row['preview_var'].set(f"エラー: {e}")
            row['preview_label'].configure(foreground='red')

    def _on_sweep_rows_changed(self):
        self._refresh_sweep_dropdowns()
        self._rebuild_baseline_section()
        self._update_combo_count()

    def _update_combo_count(self):
        lengths = []
        for row in self.sweep_rows:
            spec = row['values_var'].get().strip()
            if not spec:
                self.combo_count_var.set('')
                return
            try:
                lengths.append(len(parse_values(spec)))
            except Exception:
                self.combo_count_var.set('(値の形式エラー)')
                return
        if self.combine_var.get() == 'zip' and len(self.sweep_rows) > 1:
            if len(set(lengths)) != 1:
                self.combo_count_var.set(f"⚠ 対応(zip)は個数を揃える必要があります（現在: {lengths}）")
                return
            self.combo_count_var.set(f"推定撮影数: {lengths[0]} 通り")
        else:
            total = 1
            for n in lengths:
                total *= n
            warn = "　⚠多め（時間がかかります）" if total > 50 else ""
            self.combo_count_var.set(f"推定撮影数: {total} 通り{warn}")

    # -------------------------------------------------------------- logic

    def _on_fps_changed(self, *_):
        self.fps = int(self.fps_var.get())
        self._update_warmup_seconds()

    def _update_warmup_seconds(self, *_):
        try:
            n = int(self.warmup_var.get())
        except (tk.TclError, ValueError):
            return
        secs = n / self.fps if self.fps else 0
        self.warmup_seconds_var.set(f"≈ {secs:.2f}秒（fps={self.fps}）")

    def _save_current_baseline_entries(self):
        for name, entry in self.baseline_entries.items():
            text = entry.get().strip()
            if text == '':
                self.baseline_values.pop(name, None)
                continue
            try:
                v = float(text)
                if v.is_integer():
                    v = int(v)
                self.baseline_values[name] = v
            except ValueError:
                pass  # 無効な入力はそのまま（保存時に改めてエラーにする）

    def _reset_baseline_to_profile(self):
        self.baseline_values = dict(self.profile_baseline)
        self._rebuild_baseline_section()

    def _rebuild_baseline_section(self):
        self._save_current_baseline_entries()
        used = self._used_names()

        for child in self.baseline_frame.winfo_children():
            child.destroy()
        self.baseline_entries = {}

        r = 0
        for name, meta in PARAM_META.items():
            if name in used:
                continue
            label = meta['label'] + (f" [{meta['unit']}]" if meta['unit'] else '')
            ttk.Label(self.baseline_frame, text=label, width=30).grid(row=r, column=0, sticky='w', padx=2, pady=(4, 0))
            entry = ttk.Entry(self.baseline_frame, width=12)
            val = self.baseline_values.get(name, '')
            entry.insert(0, str(val))
            entry.grid(row=r, column=1, sticky='w', padx=2, pady=(4, 0))
            profile_val = self.profile_baseline.get(name)
            default_text = f"(プロファイル既定: {profile_val})" if profile_val is not None else "(プロファイルに項目なし)"
            ttk.Label(self.baseline_frame, text=default_text, foreground='gray')\
                .grid(row=r, column=2, sticky='w', padx=6, pady=(4, 0))
            self.baseline_entries[name] = entry
            r += 1
            # SDK側の範囲・目安（実測値と分けて表示。行を分けているのは、
            # 生プロファイル値と混同しないよう視覚的に区別するため）
            ttk.Label(self.baseline_frame, text=meta['hint'], foreground='#0a6',
                      wraplength=760, justify='left').grid(row=r, column=0, columnspan=3,
                                                            sticky='w', padx=2, pady=(0, 4))
            r += 1

    def _build_config(self):
        self._save_current_baseline_entries()

        sweeps_cfg = []
        names = []
        for row in self.sweep_rows:
            name = self.display_to_name[row['param_var'].get()]
            spec = row['values_var'].get().strip()
            if not spec:
                raise ValueError(f"「{PARAM_META[name]['label']}」の値を入力してください（例: 1000,2000,3000）")
            parse_values(spec)  # バリデーション（不正なら例外）
            if name in names:
                raise ValueError(f"同じパラメータが複数行で選択されています: {name}")
            names.append(name)
            sweeps_cfg.append({'param': name, 'values': spec})

        combine = self.combine_var.get()
        if combine == 'zip' and len(sweeps_cfg) > 1:
            lengths = {len(parse_values(s['values'])) for s in sweeps_cfg}
            if len(lengths) != 1:
                raise ValueError("対応（zip）モードでは全パラメータの値の個数を揃えてください")

        baseline_overrides = {k: v for k, v in self.baseline_values.items() if k not in names}

        return {
            'sweeps': sweeps_cfg,
            'combine': combine,
            'mode': self.mode_var.get(),
            'fps': int(self.fps_var.get()),
            'auto_fps_adjust': bool(self.auto_fps_adjust_var.get()),
            'warmup_frames': int(self.warmup_var.get()),
            'pause': float(self.pause_var.get()),
            'pointcloud': bool(self.pointcloud_var.get()),
            'auto_reference': bool(self.auto_reference_var.get()),
            'tag': self.tag_var.get().strip() or None,
            'baseline_overrides': baseline_overrides,
            'generated_at': datetime.now().isoformat(timespec='seconds'),
            'profile': self.profile_path,
        }

    def on_save(self, start):
        try:
            config = self._build_config()
        except Exception as e:
            messagebox.showerror('入力エラー', str(e))
            return

        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        param_tag = '+'.join(s['param'] for s in config['sweeps'])
        path = CONFIG_DIR / f"{ts}_{param_tag}.json"
        with open(path, 'w') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        self._log(f"設定を保存しました: {path}")

        if start:
            self._start_capture(path)

    def _start_capture(self, config_path):
        if self.proc is not None:
            messagebox.showwarning('実行中', 'すでに撮影が実行中です。')
            return
        cmd = [sys.executable, str(SWEEP_SCRIPT), '--config', str(config_path)]
        self._log(f"起動: {' '.join(cmd)}")
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True, bufsize=1)
        self.start_button.config(state='disabled')
        self.abort_button.config(state='normal')
        threading.Thread(target=self._read_proc_output, daemon=True).start()

    def _read_proc_output(self):
        proc = self.proc
        for line in proc.stdout:
            self.log_queue.put(line.rstrip('\n'))
        proc.wait()
        self.log_queue.put(f"__DONE__{proc.returncode}")

    def _poll_log_queue(self):
        try:
            while True:
                line = self.log_queue.get_nowait()
                if line.startswith('__DONE__'):
                    code = line[len('__DONE__'):]
                    self._log(f"--- 終了しました (code={code}) ---")
                    self.proc = None
                    self.start_button.config(state='normal')
                    self.abort_button.config(state='disabled')
                else:
                    self._log(line)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_log_queue)

    def _abort(self):
        if self.proc is None:
            return
        # プレビューウィンドウで[q]を押す通常終了と違い、metadata.json等が
        # 保存されないまま強制終了する点に注意（可能なら[q]キーでの終了を推奨）
        self.proc.terminate()
        self._log("強制終了を要求しました（未保存のまま終了する場合があります。"
                   "可能なら撮影ウィンドウで[q]キーを使ってください）")

    def _log(self, text):
        self.log_widget.configure(state='normal')
        self.log_widget.insert('end', text + '\n')
        self.log_widget.see('end')
        self.log_widget.configure(state='disabled')


def main():
    root = tk.Tk()
    ParamSweepGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
