#!/usr/bin/env python3
"""param_tune配下のツール群（撮影/再開・検出集計・HDR合成）をタブでまとめて操作するGUI。

- 「撮影」タブ: param_sweep.py の設定をフォームで作成し、そのまま撮影開始
  （振るパラメータ・値の範囲・モード・IR保存・点群保存・中断セッションの再開など）
- 「検出集計」タブ: detect_eval.py にセッションディレクトリを渡してYOLO集計を実行
- 「HDR合成」タブ: hdr_compose.py に露光違いのセッションを渡して画素ごとの合成を実行

いずれもJSON設定を tools/param_tune/configs/ に保存した上でサブプロセスとして
各スクリプトを起動する（同時に1つだけ実行可能。実行ログはウィンドウ下部で共有）。

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

from common import PARAM_META, parse_values, load_profile_baseline, list_sessions
from utils import load_config

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

SCRIPT_DIR          = Path(__file__).resolve().parent
SWEEP_SCRIPT        = SCRIPT_DIR / 'param_sweep.py'
DETECT_EVAL_SCRIPT  = SCRIPT_DIR / 'detect_eval.py'
HDR_COMPOSE_SCRIPT  = SCRIPT_DIR / 'hdr_compose.py'
CONFIG_DIR          = SCRIPT_DIR / 'configs'


def _display_for(name):
    meta = PARAM_META[name]
    unit = f"[{meta['unit']}]" if meta['unit'] else ''
    return f"{meta['label']}{unit} ({name})"


def _add_session_picker(parent, path_var, param_tune_dir, on_change=None):
    """セッションディレクトリ選択用の共通ウィジェット行（Entry + 参照 + 最新候補一覧）を
    parent に構築する。detect_eval / hdr_compose / sweepの再開先選択で共用する。
    戻り値: 一覧を再読込するための refresh() 関数（初期表示にも使う）。
    """
    row = ttk.Frame(parent)
    row.pack(fill='x', pady=(2, 2))
    entry = ttk.Entry(row, textvariable=path_var, width=60)
    entry.pack(side='left', fill='x', expand=True)

    def _browse():
        d = filedialog.askdirectory(initialdir=str(Path(param_tune_dir).expanduser()))
        if d:
            path_var.set(d)
            if on_change:
                on_change()

    ttk.Button(row, text='参照...', command=_browse).pack(side='left', padx=(4, 0))

    list_row = ttk.Frame(parent)
    list_row.pack(fill='x', pady=(2, 4))
    ttk.Label(list_row, text='最近のセッション:').pack(side='left')
    combo_var = tk.StringVar()
    combo = ttk.Combobox(list_row, textvariable=combo_var, state='readonly', width=64)
    combo.pack(side='left', padx=(4, 0), fill='x', expand=True)

    sessions = {}

    def refresh():
        sessions.clear()
        items = list_sessions(param_tune_dir)
        for it in items:
            sessions[it['label']] = it['path']
        combo['values'] = list(sessions.keys())

    def _on_pick(_evt=None):
        p = sessions.get(combo_var.get())
        if p is not None:
            path_var.set(str(p))
            if on_change:
                on_change()

    combo.bind('<<ComboboxSelected>>', _on_pick)
    ttk.Button(list_row, text='更新', command=refresh).pack(side='left', padx=(4, 0))

    refresh()
    return refresh


class App:
    """タブ共通の実行制御（同時に1プロセスまで）とログ表示を提供する入れ物。"""

    def __init__(self, root):
        self.root = root
        root.title('NYX660 param_tune ツール')
        root.geometry('980x860')
        root.minsize(760, 520)

        self.cfg = load_config()
        self.param_tune_dir = self.cfg['output']['param_tune_dir']

        self.proc = None
        self.log_queue = queue.Queue()
        self._on_done_callback = None

        self._build_shell()
        SweepTab(self.tab_sweep, self)
        DetectEvalTab(self.tab_detect, self)
        HdrComposeTab(self.tab_hdr, self)

        self.root.after(100, self._poll_log_queue)

    # ------------------------------------------------------------------ UI

    def _build_shell(self):
        btn_frame = ttk.Frame(self.root, padding=8)
        btn_frame.pack(side='bottom', fill='x')
        self.abort_button = ttk.Button(btn_frame, text="実行中の処理を強制終了", command=self._abort, state='disabled')
        self.abort_button.pack(side='left')
        self.status_var = tk.StringVar(value='待機中')
        ttk.Label(btn_frame, textvariable=self.status_var, foreground='gray').pack(side='left', padx=(8, 0))

        log_frame = ttk.LabelFrame(self.root, text="実行ログ（撮影・検出集計・HDR合成で共有）", padding=4)
        log_frame.pack(side='bottom', fill='x', padx=8, pady=(0, 8))
        self.log_widget = tk.Text(log_frame, height=10, state='disabled', wrap='word')
        self.log_widget.pack(fill='both', expand=True, side='left')
        log_scroll = ttk.Scrollbar(log_frame, command=self.log_widget.yview)
        log_scroll.pack(fill='y', side='right')
        self.log_widget['yscrollcommand'] = log_scroll.set

        notebook = ttk.Notebook(self.root)
        notebook.pack(side='top', fill='both', expand=True, padx=8, pady=8)
        self.tab_sweep  = ttk.Frame(notebook)
        self.tab_detect = ttk.Frame(notebook)
        self.tab_hdr    = ttk.Frame(notebook)
        notebook.add(self.tab_sweep,  text='撮影 (param_sweep)')
        notebook.add(self.tab_detect, text='検出集計 (detect_eval)')
        notebook.add(self.tab_hdr,    text='HDR合成 (hdr_compose)')

    # -------------------------------------------------------- process runner

    def run_process(self, cmd, on_done=None):
        """cmdをサブプロセスとして起動する。既に何か実行中なら警告して何もしない。
        on_done(returncode) は完了時にメインスレッドから呼ばれる。
        """
        if self.proc is not None:
            messagebox.showwarning('実行中', 'すでに他の処理が実行中です。終了を待つか強制終了してください。')
            return False
        self.log(f"起動: {' '.join(str(c) for c in cmd)}")
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True, bufsize=1)
        self._on_done_callback = on_done
        self.abort_button.config(state='normal')
        self.status_var.set('実行中...')
        threading.Thread(target=self._read_proc_output, daemon=True).start()
        return True

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
                    self.log(f"--- 終了しました (code={code}) ---")
                    self.proc = None
                    self.abort_button.config(state='disabled')
                    self.status_var.set('待機中')
                    cb, self._on_done_callback = self._on_done_callback, None
                    if cb:
                        cb(code)
                else:
                    self.log(line)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_log_queue)

    def _abort(self):
        if self.proc is None:
            return
        self.proc.terminate()
        self.log("強制終了を要求しました（未保存のまま終了する場合があります。"
                  "撮影中ならプレビューウィンドウで[q]キーの方が安全です）")

    def log(self, text):
        self.log_widget.configure(state='normal')
        self.log_widget.insert('end', text + '\n')
        self.log_widget.see('end')
        self.log_widget.configure(state='disabled')


# ============================================================================
# 撮影タブ（旧 ParamSweepGUI 相当。IR保存・中断セッションの再開を追加）
# ============================================================================

class SweepTab:
    def __init__(self, parent, app):
        self.app = app
        self.cfg = app.cfg
        self.profile_path = self.cfg['camera'].get('params_json')
        self.profile_baseline = load_profile_baseline(self.profile_path) if self.profile_path else {}
        self.fps = int(self.cfg['camera'].get('fps', 30))

        self.baseline_values = dict(self.profile_baseline)
        self.baseline_entries = {}
        self.sweep_rows = []

        self.display_to_name = {_display_for(n): n for n in PARAM_META}
        self.name_to_display = {n: d for d, n in self.display_to_name.items()}

        self._build_widgets(parent)
        self._add_sweep_row()

    # ------------------------------------------------------------------ UI

    def _build_widgets(self, parent):
        run_row = ttk.Frame(parent, padding=8)
        run_row.pack(side='bottom', fill='x')
        ttk.Button(run_row, text="設定を保存", command=lambda: self.on_save(start=False)).pack(side='left')
        ttk.Button(run_row, text="保存して撮影開始", command=lambda: self.on_save(start=True))\
            .pack(side='left', padx=(8, 0))

        scroll_outer = ttk.Frame(parent)
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

        self.save_ir_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(form, text="IRフレーム(ir.png)も保存する（HDR合成での飽和判定用。--save-ir）",
                         variable=self.save_ir_var).grid(row=row, column=1, sticky='w', pady=3)
        row += 1

        self.auto_reference_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(form, text="Auto露光の参考撮影を含める（ToF/Color両方Auto、最初に1回のみ）",
                         variable=self.auto_reference_var).grid(row=row, column=1, sticky='w', pady=3)
        row += 1

        ttk.Label(form, text="タグ（保存フォルダ名に付加、任意）").grid(row=row, column=0, sticky='w', pady=3)
        self.tag_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.tag_var, width=30).grid(row=row, column=1, sticky='w', pady=3)
        row += 1

        # --- 再開 ---
        resume_outer = ttk.LabelFrame(scroll_frame, text="中断したスイープの再開（充電切れ等）", padding=8)
        resume_outer.pack(fill='x', padx=8, pady=(4, 4))
        self.resume_enabled_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(resume_outer, text="既存セッションを再開する（撮影済みの組み合わせは撮り直さずスキップ）",
                         variable=self.resume_enabled_var).pack(anchor='w')
        ttk.Label(resume_outer,
                  text="※ 上の「振るパラメータ」「値」「タグ」は中断時と同じ内容を指定すること（組み合わせの再計算に必要）",
                  foreground='gray', wraplength=880, justify='left').pack(anchor='w', pady=(0, 4))
        self.resume_path_var = tk.StringVar()
        _add_session_picker(resume_outer, self.resume_path_var, self.cfg['output']['param_tune_dir'])

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

        resume = None
        if self.resume_enabled_var.get():
            resume = self.resume_path_var.get().strip() or 'latest'

        return {
            'sweeps': sweeps_cfg,
            'combine': combine,
            'mode': self.mode_var.get(),
            'fps': int(self.fps_var.get()),
            'auto_fps_adjust': bool(self.auto_fps_adjust_var.get()),
            'warmup_frames': int(self.warmup_var.get()),
            'pause': float(self.pause_var.get()),
            'pointcloud': bool(self.pointcloud_var.get()),
            'save_ir': bool(self.save_ir_var.get()),
            'auto_reference': bool(self.auto_reference_var.get()),
            'tag': self.tag_var.get().strip() or None,
            'baseline_overrides': baseline_overrides,
            'resume': resume,
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
        self.app.log(f"設定を保存しました: {path}")

        if start:
            cmd = [sys.executable, str(SWEEP_SCRIPT), '--config', str(path)]
            self.app.run_process(cmd)


# ============================================================================
# 検出集計タブ
# ============================================================================

class DetectEvalTab:
    def __init__(self, parent, app):
        self.app = app
        form = ttk.Frame(parent, padding=8)
        form.pack(fill='both', expand=True)

        ttk.Label(form, text="detect_eval.py でセッションディレクトリのYOLO検出を集計する。",
                  foreground='gray', wraplength=880, justify='left').pack(anchor='w')

        session_outer = ttk.LabelFrame(form, text="対象セッション（空欄なら最新セッションを自動選択）", padding=8)
        session_outer.pack(fill='x', pady=(8, 4))
        self.session_var = tk.StringVar()
        _add_session_picker(session_outer, self.session_var, app.param_tune_dir)

        opt_frame = ttk.Frame(form)
        opt_frame.pack(fill='x', pady=(4, 4))
        opt_frame.columnconfigure(1, weight=1)

        ttk.Label(opt_frame, text="モデル（空欄でconfig.yamlの既定）").grid(row=0, column=0, sticky='w', pady=3)
        model_row = ttk.Frame(opt_frame)
        model_row.grid(row=0, column=1, sticky='we')
        self.model_var = tk.StringVar()
        ttk.Entry(model_row, textvariable=self.model_var, width=50).pack(side='left', fill='x', expand=True)

        def _browse_model():
            f = filedialog.askopenfilename(initialdir=str(SCRIPT_DIR.parent.parent / 'nyx660_script' / 'model'),
                                            filetypes=[('YOLO model', '*.pt'), ('All files', '*.*')])
            if f:
                self.model_var.set(f)

        ttk.Button(model_row, text='参照...', command=_browse_model).pack(side='left', padx=(4, 0))

        ttk.Label(opt_frame, text="信頼度閾値（空欄でconfig.yamlの既定）").grid(row=1, column=0, sticky='w', pady=3)
        self.conf_var = tk.StringVar()
        ttk.Entry(opt_frame, textvariable=self.conf_var, width=10).grid(row=1, column=1, sticky='w', pady=3)

        ttk.Button(form, text="検出集計を実行", command=self._run).pack(anchor='w', pady=(8, 0))

    def _run(self):
        cmd = [sys.executable, str(DETECT_EVAL_SCRIPT)]
        session = self.session_var.get().strip()
        if session:
            cmd.append(session)
        model = self.model_var.get().strip()
        if model:
            cmd += ['--model', model]
        conf = self.conf_var.get().strip()
        if conf:
            try:
                float(conf)
            except ValueError:
                messagebox.showerror('入力エラー', '信頼度閾値は数値で指定してください')
                return
            cmd += ['--conf', conf]
        self.app.run_process(cmd)


# ============================================================================
# HDR合成タブ
# ============================================================================

class HdrComposeTab:
    def __init__(self, parent, app):
        self.app = app
        form = ttk.Frame(parent, padding=8)
        form.pack(fill='both', expand=True)

        ttk.Label(form,
                  text="hdr_compose.py で、同一の静止シーンを露光違いで撮影した複数のdepth(+IR)を"
                       "画素ごとに合成する。撮影は「撮影」タブで対象パラメータの値を振り、"
                       "ir_awareモードを使うなら「IRフレームも保存する」を有効にしておくこと。",
                  foreground='gray', wraplength=880, justify='left').pack(anchor='w')

        session_outer = ttk.LabelFrame(form, text="対象セッション（露光を振って撮影したセッション）", padding=8)
        session_outer.pack(fill='x', pady=(8, 4))
        self.session_var = tk.StringVar()
        _add_session_picker(session_outer, self.session_var, app.param_tune_dir)

        opt_frame = ttk.Frame(form)
        opt_frame.pack(fill='x', pady=(4, 4))
        opt_frame.columnconfigure(1, weight=1)
        r = 0

        ttk.Label(opt_frame, text="対象パラメータ").grid(row=r, column=0, sticky='w', pady=3)
        exposure_params = [n for n in PARAM_META
                            if n in ('tof_exposure', 'color_exposure', 'color_aec_max_exposure_time')]
        self.display_to_name = {_display_for(n): n for n in exposure_params}
        self.param_var = tk.StringVar(value=_display_for('tof_exposure'))
        ttk.Combobox(opt_frame, textvariable=self.param_var, state='readonly', width=32,
                     values=list(self.display_to_name.keys())).grid(row=r, column=1, sticky='w', pady=3)
        r += 1

        ttk.Label(opt_frame, text="合成する値（1000,3000,5000 または 1000:8000:1000）")\
            .grid(row=r, column=0, sticky='w', pady=3)
        self.values_var = tk.StringVar()
        ttk.Entry(opt_frame, textvariable=self.values_var, width=40).grid(row=r, column=1, sticky='w', pady=3)
        r += 1

        ttk.Label(opt_frame, text="合成モード").grid(row=r, column=0, sticky='w', pady=3)
        mode_frame = ttk.Frame(opt_frame)
        mode_frame.grid(row=r, column=1, sticky='w')
        self.mode_var = tk.StringVar(value='first_valid')
        ttk.Radiobutton(mode_frame, text='first_valid（短い露光を優先）', variable=self.mode_var,
                         value='first_valid', command=self._on_mode_changed).pack(side='left')
        ttk.Radiobutton(mode_frame, text='ir_aware（IR飽和を除外、要--save-ir）', variable=self.mode_var,
                         value='ir_aware', command=self._on_mode_changed).pack(side='left', padx=(12, 0))
        r += 1

        ttk.Label(opt_frame, text="IR飽和しきい値（0-255）").grid(row=r, column=0, sticky='w', pady=3)
        self.ir_sat_var = tk.IntVar(value=250)
        self.ir_sat_spin = ttk.Spinbox(opt_frame, from_=0, to=255, textvariable=self.ir_sat_var, width=6)
        self.ir_sat_spin.grid(row=r, column=1, sticky='w', pady=3)
        r += 1

        ttk.Label(opt_frame, text="タグ（出力フォルダ名 hdr_compose_<tag>、空欄で時刻）")\
            .grid(row=r, column=0, sticky='w', pady=3)
        self.tag_var = tk.StringVar()
        ttk.Entry(opt_frame, textvariable=self.tag_var, width=20).grid(row=r, column=1, sticky='w', pady=3)
        r += 1

        self._on_mode_changed()

        ttk.Button(form, text="HDR合成を実行", command=self._run).pack(anchor='w', pady=(8, 0))

    def _on_mode_changed(self):
        self.ir_sat_spin.configure(state='normal' if self.mode_var.get() == 'ir_aware' else 'disabled')

    def _run(self):
        session = self.session_var.get().strip()
        if not session:
            messagebox.showerror('入力エラー', '対象セッションを指定してください')
            return
        values = self.values_var.get().strip()
        if not values:
            messagebox.showerror('入力エラー', '合成する値を指定してください（例: 1000,3000,5000）')
            return
        try:
            parse_values(values)
        except Exception as e:
            messagebox.showerror('入力エラー', f'値の形式が不正です: {e}')
            return

        cmd = [sys.executable, str(HDR_COMPOSE_SCRIPT), session,
               '--param', self.display_to_name[self.param_var.get()],
               '--values', values,
               '--mode', self.mode_var.get()]
        if self.mode_var.get() == 'ir_aware':
            cmd += ['--ir-saturation', str(self.ir_sat_var.get())]
        tag = self.tag_var.get().strip()
        if tag:
            cmd += ['--tag', tag]
        self.app.run_process(cmd)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
