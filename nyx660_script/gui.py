"""nyx660_syutoku 統合GUI（ランチャー）

    python3 nyx660_script/gui.py

やりたいことのボタンを押すと、対応するスクリプトを別プロセスで起動する。
スクリプト単体での使い方（コマンドライン）は今までどおり使える。
real_syutoku の real_script/gui.py と同じ画面構成。
"""
import codecs
import signal
import subprocess
import sys
import threading
import queue
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from utils import load_config

CFG = load_config()

# スクリプトをこれ経由で起動する。「停止」(SIGINT) 時に KeyboardInterrupt のトレースバックを出さない
_RUNNER = """\
import runpy, sys
sys.argv = sys.argv[1:]
try:
    runpy.run_path(sys.argv[0], run_name='__main__')
except KeyboardInterrupt:
    print('\\n中断しました')
"""

# NYX660 の検出。GUI 本体で SDK を初期化したままにすると撮影スクリプトと競合しうるので、
# 別プロセスで列挙だけして終わらせる
_DETECT = """\
import sys
sys.path.insert(0, sys.argv[1])
from utils import load_config, init_sdk
init_sdk(load_config())
from API.ScepterDS_api import ScepterTofCam
cam = ScepterTofCam()
n = cam.scGetDeviceCount(3000)
if n > 0:
    ret, infos = cam.scGetDeviceInfoList(n)
    for i in infos[:n]:
        print('DEV', i.productName.decode(), i.serialNumber.decode(), i.ip.decode(), sep='\\t')
"""


def _out_dir(kind):
    """各機能の保存先フォルダ（config.yaml から。load_config で絶対パスに解決済み）。"""
    out = CFG['output']
    return Path({
        'images':     out['images_dir'],
        'click':      str(Path(out['images_dir']).parent / 'click_test_data'),
        'timelapse':  out['timelapse_dir'],
        'pointcloud': out['pointcloud_dir'],
        'mp4':        out['mp4_dir'],
    }[kind])


class App:
    def __init__(self, root):
        self.root = root
        self.proc = None
        self.out_kind = 'images'
        self.model_path = str(ROOT / CFG.get('model', {}).get('yolo_path', 'model/best.pt'))
        self.log_q = queue.Queue()

        root.title('nyx660_syutoku')
        root.minsize(620, 560)
        self._build_header()
        self._build_tabs()
        self._build_footer()

        root.protocol('WM_DELETE_WINDOW', self._on_close)
        root.after(100, self._poll_log)
        self.refresh_camera()

    # ------------------------------------------------------------------ 画面
    def _build_header(self):
        f = ttk.Frame(self.root, padding=(12, 10, 12, 4))
        f.pack(fill='x')

        ttk.Label(f, text='カメラ', width=8).grid(row=0, column=0, sticky='w')
        self.cam_var = tk.StringVar(value='検出中...')
        ttk.Label(f, textvariable=self.cam_var).grid(row=0, column=1, sticky='w')
        ttk.Button(f, text='再検出', command=self.refresh_camera).grid(row=0, column=2, padx=(6, 0))

        ttk.Label(f, text='タグ', width=8).grid(row=1, column=0, sticky='w', pady=(6, 0))
        self.tag_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.tag_var, width=24).grid(row=1, column=1, sticky='w', pady=(6, 0))
        ttk.Label(f, text='任意。保存フォルダ名の末尾に付きます（例: greenhouse）',
                  foreground='gray').grid(row=2, column=1, sticky='w')
        f.columnconfigure(1, weight=1)

    def _build_tabs(self):
        nb = ttk.Notebook(self.root, padding=(12, 6))
        nb.pack(fill='both', expand=True)
        self.task_buttons = []

        # --- 画像 ---
        t = self._tab(nb, '画像')
        self._task(t, '全データを1枚取得', 'RGB・深度・IR・PLYを、露光安定待ちの後にまとめて保存します',
                   '約2秒後に自動保存して終了', 'collect/dataset_collect_photo.py', 'images',
                   args=lambda: ['--once'])
        self._task(t, '全データを1枚ずつ撮る', 'RGB・深度・IR・PLYを [s] キーで一括保存します',
                   '[s] 保存  [q] 終了', 'collect/dataset_collect_photo.py', 'images')
        self._task(t, '連続で撮る', '[Enter] で開始すると毎フレーム保存し続けます',
                   '[Enter] 開始  [q] 終了', 'collect/dataset_collect.py', 'images')

        # --- 点群 ---
        t = self._tab(nb, '点群')
        opt = ttk.Frame(t)
        opt.pack(fill='x', pady=(0, 6))
        self.pc_mode = tk.StringVar(value='auto')
        self.pc_frames = tk.IntVar(value=CFG['pointcloud']['capture_frames'])
        self.pc_align = tk.BooleanVar(value=False)
        ttk.Radiobutton(opt, text='自動で', variable=self.pc_mode, value='auto').grid(row=0, column=0, sticky='w')
        ttk.Spinbox(opt, from_=1, to=1000, textvariable=self.pc_frames, width=6).grid(row=0, column=1)
        ttk.Label(opt, text='枚').grid(row=0, column=2, sticky='w')
        ttk.Radiobutton(opt, text='手動で（[s] で1枚ずつ）', variable=self.pc_mode,
                        value='manual').grid(row=0, column=3, sticky='w', padx=(16, 0))
        ttk.Checkbutton(opt, text='color を depth に位置合わせして保存（BBox切り出しに使う場合）',
                        variable=self.pc_align).grid(row=1, column=0, columnspan=4, sticky='w', pady=(4, 0))
        self._task(t, '点群を撮る', '上の設定で点群を撮影します',
                   '自動: [Enter] 開始 / 手動: [s] 保存  [q] 終了',
                   'collect/dataset_point_collect.py', 'pointcloud', args=self._pc_args)
        self._task(t, '点群をつなぎ合わせる', '撮影したフォルダを選んで、複数の点群を1つにまとめます',
                   'フォルダを選択', 'process/point_merge.py', 'pointcloud',
                   args=lambda: self._ask_dir('pointcloud', '点群のセッションフォルダを選択'),
                   camera=False)

        # --- 動画 ---
        t = self._tab(nb, '動画')
        self._task(t, '録画する', 'カラー動画と depth 動画(.mp4)を保存します',
                   '[Enter] 開始  [q] 終了', 'record/mp4_collect.py', 'mp4')
        self._task(t, 'YOLO検出付きで録画する', '検出結果と距離を描いた動画(.mp4)を保存します',
                   'モデルを選択 → [Enter] 開始  [q] 終了', 'record/record_with_yolo.py', 'mp4',
                   args=self._model_args)

        # --- タイムラプス ---
        t = self._tab(nb, 'タイムラプス')
        opt = ttk.Frame(t)
        opt.pack(fill='x', pady=(0, 6))
        self.tl_interval = tk.IntVar(value=5)
        self.tl_hours = tk.DoubleVar(value=12)
        self.tl_detect = tk.BooleanVar(value=False)
        ttk.Spinbox(opt, from_=1, to=1440, textvariable=self.tl_interval, width=6).grid(row=0, column=0)
        ttk.Label(opt, text='分ごとに').grid(row=0, column=1, sticky='w')
        ttk.Spinbox(opt, from_=0.1, to=240, increment=0.5, textvariable=self.tl_hours,
                    width=6).grid(row=0, column=2, padx=(12, 0))
        ttk.Label(opt, text='時間撮影する').grid(row=0, column=3, sticky='w')
        ttk.Checkbutton(opt, text='YOLO検出もする（開始時にモデルを選択）',
                        variable=self.tl_detect).grid(row=1, column=0, columnspan=4, sticky='w', pady=(4, 0))
        self._task(t, 'タイムラプスを開始', '上の設定で定点撮影します。止めるときは下の「停止」',
                   '「停止」で終了', 'collect/timelapse_detect.py', 'timelapse', args=self._tl_args)
        self._task(t, '検出結果をグラフにする', 'YOLO検出付きで撮ったフォルダを選んでグラフを作ります',
                   'フォルダを選択', 'process/timelapse_analysis.py', 'timelapse',
                   args=lambda: self._ask_dir('timelapse', 'タイムラプスのセッションフォルダを選択'),
                   camera=False)

        # --- 推論・アノテーション ---
        t = self._tab(nb, '推論・アノテーション')
        self._task(t, 'YOLOで検出する', '検出結果と距離をリアルタイムで表示します（保存はしません）',
                   'モデルを選択 → [q] 終了', 'detect/yolo_detection.py', 'images',
                   args=self._model_args)
        self._task(t, 'クリックで座標を記録', '画像上の対象をクリックし、画像と座標を保存します',
                   'クリック → [s] 保存  [q] 終了', 'click_script/click_dataset.py', 'click')
        self._task(t, 'BBoxアノテーション', '画像フォルダを選び、YOLO形式のラベルを付けます',
                   'フォルダを選択 → [d] 次へ  [s] 保存  [q] 終了', 'click_script/bbox_click.py', 'images',
                   camera=False)

    def _tab(self, nb, title):
        f = ttk.Frame(nb, padding=12)
        nb.add(f, text=f'  {title}  ')
        return f

    def _task(self, parent, title, desc, keys, script, out, args=None, camera=True):
        """機能1つ分のボタンと説明。

        args   : 追加引数を返す関数（None を返したら中止）
        camera : カメラを使う（--tag を渡し、未検出なら警告する）
        """
        f = ttk.Frame(parent)
        f.pack(fill='x', pady=4)
        b = ttk.Button(f, text=title, width=22,
                       command=lambda: self.run(title, script, out, args, camera))
        b.grid(row=0, column=0, rowspan=2, sticky='nw', padx=(0, 10))
        ttk.Label(f, text=desc).grid(row=0, column=1, sticky='w')
        ttk.Label(f, text=f'操作: {keys}', foreground='gray').grid(row=1, column=1, sticky='w')
        self.task_buttons.append(b)

    def _build_footer(self):
        f = ttk.Frame(self.root, padding=(12, 4, 12, 12))
        f.pack(fill='both')
        bar = ttk.Frame(f)
        bar.pack(fill='x')
        self.status = tk.StringVar(value='待機中')
        ttk.Label(bar, textvariable=self.status).pack(side='left')
        ttk.Button(bar, text='保存先を開く', command=self.open_out).pack(side='right')
        self.stop_btn = ttk.Button(bar, text='停止', command=self.stop, state='disabled')
        self.stop_btn.pack(side='right', padx=6)

        self.log = tk.Text(f, height=10, wrap='char', state='disabled',
                           background='#1e1e1e', foreground='#dddddd')
        self.log.pack(fill='both', expand=True, pady=(6, 0))

    # ------------------------------------------------------------------ カメラ
    def refresh_camera(self):
        self.cam_var.set('検出中...（数秒かかります）')
        self.camera_found = False

        def work():
            try:
                r = subprocess.run([sys.executable, '-c', _DETECT, str(ROOT)], cwd=str(ROOT),
                                   capture_output=True, text=True, timeout=20)
                devs = [l.split('\t')[1:] for l in r.stdout.splitlines() if l.startswith('DEV\t')]
                err = None if r.returncode == 0 else (r.stderr.strip().splitlines() or ['不明なエラー'])[-1]
            except subprocess.TimeoutExpired:
                devs, err = [], 'タイムアウト'
            self.root.after(0, lambda: self._set_camera(devs, err))
        threading.Thread(target=work, daemon=True).start()

    def _set_camera(self, devs, err):
        self.camera_found = bool(devs)
        if devs:
            name, serial, ip = devs[0]
            self.cam_var.set(f'{name}  (シリアル: {serial}  IP: {ip})')
        elif err:
            self.cam_var.set(f'検出できません（SDK の場所 config.yaml の sdk_path を確認）: {err}')
        else:
            self.cam_var.set('カメラが見つかりません（LAN 接続を確認して「再検出」）')

    # ------------------------------------------------------------------ 引数
    def _pc_args(self):
        a = ['--mode', self.pc_mode.get()]
        if self.pc_mode.get() == 'auto':
            a += ['--frames', str(self.pc_frames.get())]
        if self.pc_align.get():
            a.append('--align')
        return a

    def _tl_args(self):
        return ['--interval', str(int(self.tl_interval.get()) * 60),
                '--duration', str(self.tl_hours.get())] + (['--detect'] if self.tl_detect.get() else [])

    def _model_args(self):
        p = filedialog.askopenfilename(
            title='YOLOモデル（.pt）を選択', initialdir=str(Path(self.model_path).parent),
            initialfile=Path(self.model_path).name,
            filetypes=[('YOLO model', '*.pt'), ('すべて', '*.*')])
        if not p:
            return None
        self.model_path = p
        return ['--model', p]

    def _ask_dir(self, kind, title):
        p = filedialog.askdirectory(title=title, initialdir=str(_out_dir(kind)), mustexist=True)
        return [p] if p else None

    # ------------------------------------------------------------------ 実行
    def run(self, title, script, out, args_fn, camera):
        if self.proc is not None:
            return
        args = []
        if camera:
            if not self.camera_found and not messagebox.askyesno(
                    'カメラ未検出', 'NYX660 が検出されていません。このまま起動しますか？'):
                return
            if self.tag_var.get().strip():
                args += ['--tag', self.tag_var.get().strip()]
        if args_fn is not None:
            extra = args_fn()
            if extra is None:       # ファイル選択などをキャンセル
                return
            args += extra

        cmd = [sys.executable, '-u', '-c', _RUNNER, str(ROOT / script)] + args
        self._append(f"\n$ python3 {script} {' '.join(args)}\n")
        self.proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        self.out_kind = out
        self.stop_pressed = False
        self.status.set(f'実行中: {title}')
        self._set_running(True)
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()

    def _reader(self, proc):
        dec = codecs.getincrementaldecoder('utf-8')(errors='replace')   # 文字の途中で切れても化けない
        for chunk in iter(lambda: proc.stdout.read1(4096), b''):
            self.log_q.put(dec.decode(chunk))
        code = proc.wait()
        self.log_q.put(None)
        self.log_q.put(code)

    def stop(self):
        if self.proc is None:
            return
        if not self.stop_pressed:
            # Ctrl+C と同じ。スクリプト側の後始末（metadata 書き出し・カメラ解放）が走る
            self.proc.send_signal(signal.SIGINT)
            self.stop_pressed = True
            self.status.set('停止中...（もう一度押すと強制終了）')
        else:
            self.proc.kill()

    def _set_running(self, running):
        for b in self.task_buttons:
            b.state(['disabled'] if running else ['!disabled'])
        self.stop_btn.state(['!disabled'] if running else ['disabled'])

    def _poll_log(self):
        try:
            while True:
                item = self.log_q.get_nowait()
                if item is None:
                    code = self.log_q.get()
                    self._append("\n[終了しました]\n" if code == 0 or self.stop_pressed
                                 else f"\n[エラーで終了しました（コード {code}）]\n")
                    self.proc = None
                    self.status.set('待機中')
                    self._set_running(False)
                else:
                    self._append(item)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_log)

    def _append(self, text):
        self.log.configure(state='normal')
        # '\r' で同じ行を上書きする進捗表示（saved: N frames など）に対応する
        parts = text.split('\r')
        for i, part in enumerate(parts):
            if i > 0:
                self.log.delete('end-1c linestart', 'end-1c')
            self.log.insert('end', part)
        self.log.see('end')
        self.log.configure(state='disabled')

    def open_out(self):
        d = _out_dir(self.out_kind)
        d.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(['xdg-open', str(d)])

    def _on_close(self):
        if self.proc is not None:
            if not messagebox.askyesno('終了', '実行中の処理を停止して終了しますか？'):
                return
            self.proc.send_signal(signal.SIGINT)
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
