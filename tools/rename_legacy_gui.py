#!/usr/bin/env python3
"""旧命名データを新命名規則へ変換する GUI ツール。

    python3 tools/rename_legacy_gui.py

変換元フォルダを複数選んで一覧に並べ、「変換前 → 変換後」のファイル名を
確認してから実行できる。変換ロジックは CLI 版（rename_legacy.py）と共通。

    {cam}_{YYMMDD}_{HHMMSS}_{NNNNN}_{mod}.{ext}

既定はコピーなので原データは残る（「移動」を選んだ場合のみ原データが移る）。
tkinter が必要: sudo apt install python3-tk
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from rename_legacy import build_plans, apply_plan

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    from tkinter.scrolledtext import ScrolledText
except ImportError:
    print("tkinter が必要です: sudo apt install python3-tk")
    sys.exit(1)

CAM_CHOICES = ['自動判別', 'd435', 'd405', 'nyx']

# 1セッションあたりのプレビュー表示上限（タイムラプス等で数千件になるため）
PREVIEW_LIMIT = 500


class RenameLegacyGUI:
    def __init__(self, master):
        self.master = master
        master.title('旧データ命名変換ツール')
        master.geometry('980x720')

        self.sources = []      # 変換元ディレクトリ（複数可）
        self.plans   = []      # build_plans() の結果
        self.skipped = []

        self._build_sources(master)
        self._build_options(master)
        self._build_preview(master)
        self._build_footer(master)
        self._invalidate()

    # ------------------------------------------------------------------ UI

    def _build_sources(self, master):
        frame = ttk.LabelFrame(master, text='1. 変換元フォルダ（複数選択可）')
        frame.pack(fill='x', padx=10, pady=(10, 5))

        self.src_list = tk.Listbox(frame, height=4, selectmode='extended')
        self.src_list.pack(side='left', fill='both', expand=True, padx=(8, 4), pady=8)

        btns = ttk.Frame(frame)
        btns.pack(side='left', fill='y', padx=(0, 8), pady=8)
        ttk.Button(btns, text='フォルダを追加...', command=self.add_source).pack(fill='x')
        ttk.Button(btns, text='選択を削除',       command=self.remove_source).pack(fill='x', pady=(4, 0))
        ttk.Button(btns, text='すべて削除',       command=self.clear_sources).pack(fill='x', pady=(4, 0))

        ttk.Label(frame, text='').pack()   # 余白調整

    def _build_options(self, master):
        frame = ttk.LabelFrame(master, text='2. 設定')
        frame.pack(fill='x', padx=10, pady=5)

        inner = ttk.Frame(frame)
        inner.pack(fill='x', padx=8, pady=8)
        inner.columnconfigure(1, weight=1)

        ttk.Label(inner, text='カメラコード:').grid(row=0, column=0, sticky='w')
        self.cam_var = tk.StringVar(value=CAM_CHOICES[0])
        cam_box = ttk.Combobox(inner, textvariable=self.cam_var, values=CAM_CHOICES,
                               state='readonly', width=12)
        cam_box.grid(row=0, column=1, sticky='w', padx=(6, 0))
        cam_box.bind('<<ComboboxSelected>>', lambda e: self._invalidate())
        ttk.Label(inner, text='（フォルダ名から判別できない場合は明示指定が必要）',
                  foreground='gray').grid(row=0, column=2, sticky='w', padx=(8, 0))

        ttk.Label(inner, text='タグ（任意）:').grid(row=1, column=0, sticky='w', pady=(6, 0))
        self.tag_var = tk.StringVar()
        self.tag_var.trace_add('write', lambda *a: self._invalidate())
        ttk.Entry(inner, textvariable=self.tag_var, width=20).grid(
            row=1, column=1, sticky='w', padx=(6, 0), pady=(6, 0))
        ttk.Label(inner, text='（セッションフォルダ名にのみ付きます。ファイル名は変わりません）',
                  foreground='gray').grid(row=1, column=2, sticky='w', padx=(8, 0), pady=(6, 0))

        ttk.Label(inner, text='出力先:').grid(row=2, column=0, sticky='w', pady=(6, 0))
        out_frame = ttk.Frame(inner)
        out_frame.grid(row=2, column=1, columnspan=2, sticky='ew', padx=(6, 0), pady=(6, 0))
        out_frame.columnconfigure(0, weight=1)
        self.out_var = tk.StringVar()
        self.out_var.trace_add('write', lambda *a: self._invalidate())
        ttk.Entry(out_frame, textvariable=self.out_var).grid(row=0, column=0, sticky='ew')
        ttk.Button(out_frame, text='参照...', command=self.choose_out).grid(
            row=0, column=1, padx=(4, 0))
        ttk.Button(out_frame, text='既定に戻す', command=lambda: self.out_var.set('')).grid(
            row=0, column=2, padx=(4, 0))
        ttk.Label(inner, text='空欄なら変換元と同じ場所に新しい日付フォルダを作ります',
                  foreground='gray').grid(row=3, column=1, columnspan=2, sticky='w',
                                          padx=(6, 0))

        ttk.Label(inner, text='動作:').grid(row=4, column=0, sticky='w', pady=(6, 0))
        mode_frame = ttk.Frame(inner)
        mode_frame.grid(row=4, column=1, columnspan=2, sticky='w', padx=(6, 0), pady=(6, 0))
        self.move_var = tk.BooleanVar(value=False)
        ttk.Radiobutton(mode_frame, text='コピー（原データを残す）', value=False,
                        variable=self.move_var).pack(side='left')
        ttk.Radiobutton(mode_frame, text='移動（原データは残りません）', value=True,
                        variable=self.move_var).pack(side='left', padx=(12, 0))

    def _build_preview(self, master):
        frame = ttk.LabelFrame(master, text='3. 変換内容の確認')
        frame.pack(fill='both', expand=True, padx=10, pady=5)

        bar = ttk.Frame(frame)
        bar.pack(fill='x', padx=8, pady=(8, 4))
        ttk.Button(bar, text='プレビューを作成', command=self.preview).pack(side='left')
        ttk.Button(bar, text='すべて開く', command=lambda: self._expand(True)).pack(
            side='left', padx=(6, 0))
        ttk.Button(bar, text='すべて閉じる', command=lambda: self._expand(False)).pack(
            side='left', padx=(6, 0))
        self.status_var = tk.StringVar(value='変換元フォルダを追加してください')
        ttk.Label(bar, textvariable=self.status_var).pack(side='left', padx=(16, 0))

        tree_frame = ttk.Frame(frame)
        tree_frame.pack(fill='both', expand=True, padx=8, pady=(0, 8))
        self.tree = ttk.Treeview(tree_frame, columns=('after',), height=12)
        self.tree.heading('#0', text='変換前')
        self.tree.heading('after', text='変換後')
        self.tree.column('#0', width=520, stretch=True)
        self.tree.column('after', width=380, stretch=True)
        vsb = ttk.Scrollbar(tree_frame, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side='left', fill='both', expand=True)
        vsb.pack(side='left', fill='y')

        self.tree.tag_configure('session', font=('TkDefaultFont', 10, 'bold'))
        self.tree.tag_configure('skip', foreground='#b00')
        self.tree.tag_configure('note', foreground='#a60')

    def _build_footer(self, master):
        frame = ttk.Frame(master)
        frame.pack(fill='x', padx=10, pady=(0, 10))

        self.progress = ttk.Progressbar(frame, mode='determinate')
        self.progress.pack(fill='x', pady=(0, 6))

        row = ttk.Frame(frame)
        row.pack(fill='x')
        self.run_btn = ttk.Button(row, text='変換を実行', command=self.run)
        self.run_btn.pack(side='left')
        ttk.Button(row, text='閉じる', command=master.destroy).pack(side='right')

        self.log = ScrolledText(frame, height=6, state='disabled')
        self.log.pack(fill='x', pady=(6, 0))

    # --------------------------------------------------------------- 操作

    def add_source(self):
        d = filedialog.askdirectory(title='変換元フォルダを選択')
        if not d:
            return
        if d in self.sources:
            return
        self.sources.append(d)
        self.src_list.insert('end', d)
        self._invalidate()

    def remove_source(self):
        for i in reversed(self.src_list.curselection()):
            self.src_list.delete(i)
            del self.sources[i]
        self._invalidate()

    def clear_sources(self):
        self.src_list.delete(0, 'end')
        self.sources.clear()
        self._invalidate()

    def choose_out(self):
        d = filedialog.askdirectory(title='出力先フォルダを選択')
        if d:
            self.out_var.set(d)

    def _expand(self, opened):
        for item in self.tree.get_children():
            self.tree.item(item, open=opened)

    def _invalidate(self):
        """設定が変わったらプレビューを無効化して、実行前に必ず確認させる。"""
        self.plans, self.skipped = [], []
        self.tree.delete(*self.tree.get_children())
        self.run_btn.state(['disabled'])
        if self.sources:
            self.status_var.set('「プレビューを作成」で変換内容を確認してください')
        else:
            self.status_var.set('変換元フォルダを追加してください')

    def _write_log(self, text):
        self.log.configure(state='normal')
        self.log.insert('end', text + '\n')
        self.log.see('end')
        self.log.configure(state='disabled')

    # ------------------------------------------------------------- プレビュー

    def preview(self):
        if not self.sources:
            messagebox.showinfo('変換元がありません', '変換元フォルダを追加してください。')
            return

        cam = None if self.cam_var.get() == CAM_CHOICES[0] else self.cam_var.get()
        tag = self.tag_var.get().strip() or None
        out = self.out_var.get().strip() or None

        self.tree.delete(*self.tree.get_children())
        self.plans, self.skipped = [], []
        self.status_var.set('確認中...')
        self.master.update_idletasks()

        for src in self.sources:
            try:
                plans, skipped = build_plans(src, out=out, cam=cam, tag=tag)
            except OSError as e:
                self._write_log(f'[エラー] {src}: {e}')
                continue
            self.plans.extend(plans)
            self.skipped.extend(skipped)

        for plan in self.plans:
            head = self.tree.insert(
                '', 'end', open=False, tags=('session',),
                text=f'{plan.session_dir}  '
                     f'（{len(plan.pairs)} ファイル / {plan.shots} ショット）',
                values=(f'{plan.dst_dir}',))
            if plan.note:
                self.tree.insert(head, 'end', text=plan.note, values=('',), tags=('note',))
            for src, dst in plan.pairs[:PREVIEW_LIMIT]:
                rel = src.relative_to(plan.session_dir)
                self.tree.insert(head, 'end', text=f'    {rel}', values=(dst.name,))
            if len(plan.pairs) > PREVIEW_LIMIT:
                self.tree.insert(head, 'end', values=('',),
                                 text=f'    ... 他 {len(plan.pairs) - PREVIEW_LIMIT} 件'
                                      '（同じ規則で変換されます）')

        for session_dir, reason in self.skipped:
            self.tree.insert('', 'end', tags=('skip',),
                             text=f'[スキップ] {session_dir}', values=(reason,))

        files = sum(len(p.pairs) for p in self.plans)
        shots = sum(p.shots for p in self.plans)
        if self.plans:
            self.status_var.set(f'{len(self.plans)} セッション / {files} ファイル / '
                                f'{shots} ショット'
                                + (f'（スキップ {len(self.skipped)} 件）' if self.skipped else ''))
            self.run_btn.state(['!disabled'])
        else:
            self.status_var.set('変換対象が見つかりませんでした')
            self.run_btn.state(['disabled'])

    # ----------------------------------------------------------------- 実行

    def run(self):
        if not self.plans:
            return
        move  = self.move_var.get()
        tag   = self.tag_var.get().strip() or None
        files = sum(len(p.pairs) for p in self.plans)

        msg = (f'{len(self.plans)} セッション / {files} ファイルを'
               f'{"移動" if move else "コピー"}します。\n\n')
        if move:
            msg += '「移動」のため、変換元のファイルは残りません。\n\n'
        msg += '実行しますか？'
        if not messagebox.askyesno('確認', msg):
            return

        self.run_btn.state(['disabled'])
        self.progress.configure(maximum=files, value=0)
        done = [0]

        def on_file(src, dst, status):
            done[0] += 1
            if status == 'exists':
                self._write_log(f'[スキップ] 既に存在します: {dst}')
            if done[0] % 50 == 0 or done[0] == files:
                self.progress.configure(value=done[0])
                self.status_var.set(f'変換中... {done[0]}/{files}')
                self.master.update()

        total_written = total_existing = 0
        try:
            for plan in self.plans:
                self._write_log(f'{plan.session_dir}\n  → {plan.dst_dir}')
                written, existing = apply_plan(plan, move=move, tag=tag, on_file=on_file)
                total_written  += written
                total_existing += existing
        except OSError as e:
            messagebox.showerror('エラー', f'変換中にエラーが発生しました:\n{e}')
            self._write_log(f'[エラー] {e}')
            # 途中まで書き出している可能性があるため、プレビューし直させる
            self._invalidate()
            return
        finally:
            self.progress.configure(value=files)

        action = '移動' if move else 'コピー'
        summary = f'完了: {total_written} ファイルを{action}しました'
        if total_existing:
            summary += f'（既存のためスキップ {total_existing} 件）'
        self._write_log(summary)
        self.status_var.set(summary)
        messagebox.showinfo('完了', summary)
        self._invalidate()


def main():
    root = tk.Tk()
    RenameLegacyGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
