# 使い方詳細

[README](../README.md) の「使い方」の補足です．保存されるファイルの形式は [data_format.md](data_format.md) を参照してください．

## GUI

```bash
python3 nyx660_script/gui.py
```

real_syutoku の GUI と同じ画面構成です．

1. 上の「カメラ」に検出された NYX660（シリアル・IP）が表示されます．複数台つないでいるときはここで選びます．出ないときは LAN 接続を確認して「再検出」（原因は下のログに出ます）．
2. やりたいことのタブ（画像・点群・動画・タイムラプス・推論・アノテーション）を開き，ボタンを押します．
3. カメラ映像のウィンドウが開いたら，ボタン横の「操作」に書かれたキーで保存・終了します．
4. 途中でやめたいときは「停止」を押します（データは保存されたまま安全に終了します）．
5. 「保存先を開く」で，最後に使った機能の保存フォルダが開きます．

GUI は下記のスクリプトをボタンで起動しているだけなので，コマンドラインからも同じことができます．
細かいオプション（FPS・color 解像度など）を変えたいときはコマンドラインか `config.yaml` を使ってください．

## RGB・深度・IR・PLYの一括取得

「画像」タブの3つのボタンはどれも `collect/dataset_collect_photo.py` を起動します．

| ボタン | 内容 | CLI |
|---|---|---|
| 1枚ずつ撮る | `s` で画像を保存（PLYなし） | （オプションなし） |
| 全データを1枚ずつ撮る | `s` を押すたびにRGB・深度・IR・PLYなどを一括保存 | `--all-data` |
| 全データを1枚だけ撮る | 露光安定待ち（約2秒）の後に1枚だけ保存して自動で終了 | `--all-data --once` |

```bash
# 通常の画像保存（PLYなし）。sで保存，qで終了
python3 nyx660_script/collect/dataset_collect_photo.py

# PLYを含む全データをsで1組ずつ保存，qで終了
python3 nyx660_script/collect/dataset_collect_photo.py --all-data

# 画面を使わず1ショット保存．待ち時間は任意に変更できる
python3 nyx660_script/collect/dataset_collect_photo.py --all-data --once --no-preview --warmup 2
```

必要なフレームが揃わない場合は既定で10秒後にエラーにします（`--timeout` で変更可）．
選択した保存モードのファイルが揃ったショットだけを撮影枚数に数えます．
既存の同名ファイルは上書きせず，同じ秒に別セッションを開始する場合も保存先を分けます．

別タイミングで到着するフレームは，既定で250ms以内（低FPS時は3フレーム周期以内）の
最新データを組み合わせます．厳密な同時露光は保証せず，SDKのハードウェア時刻と
キャッシュの経過時間を各ショットに記録します．全データ撮影では，古いフレームやIR未取得のまま保存しません．

## CLI オプション

全スクリプト共通のオプション：

| オプション | 説明 | 例 |
|---|---|---|
| `--fps N` | FPS を上書き | `--fps 15` |
| `--color-size WxH` | color 解像度を上書き | `--color-size 640x480` |
| `--tag NAME` | セッションディレクトリ名にタグを付与（ファイル名には付かない） | `--tag greenhouse` |
| `--serial SERIAL` | 使うカメラをシリアル番号で指定（複数台接続時．省略時は最初に見つかった1台） | `--serial NYX_SERIAL_NUMBER` |

スクリプト固有のオプション：

| オプション | 対象スクリプト | 説明 |
|---|---|---|
| `--all-data` | dataset_collect_photo | PLY・ロスレス画像を含む全データを保存 |
| `--once` | dataset_collect_photo | 1枚保存して終了 |
| `--warmup S` / `--timeout S` / `--no-preview` | dataset_collect_photo | 露光安定待ち / フレーム待ち上限 / 画面なし（`--once` 時） |
| `--model PATH` | yolo_detection / record_with_yolo | YOLO モデル（`config.yaml` の `model.yolo_path` を上書き） |
| `--frames N` | dataset_point_collect | auto モードのフレーム数 |
| `--mode auto\|manual` | dataset_point_collect | 取得モード |
| `--align` | dataset_point_collect | color を depth 座標系（640×480）に位置合わせして保存 |
| `--sequential` | point_merge | ICP を前フレーム順に適用 |
| `--voxel-size F` | point_merge | ダウンサンプリング解像度 (m) |
| `--icp-threshold F` | point_merge | ICP 最大対応点距離 (m) |
| `--interval N` | timelapse_detect | 撮影間隔（秒） |
| `--duration H` | timelapse_detect | 継続時間（時間） |
| `--detect` | timelapse_detect | YOLO 検出ログを有効化 |
| `--relative-depth` | timelapse_detect | 深度カラーマップを相対値で正規化 |

## スクリプト別の使い方

各スクリプトはそれぞれのディレクトリから実行します．

### collect/ — データ収集

```bash
cd nyx660_script/collect

# 連続収録（Enter で開始、q で停止）
python3 dataset_collect.py

# 1ショット収録（s で保存、q で終了）
python3 dataset_collect_photo.py

# 点群収集
python3 dataset_point_collect.py               # auto 50フレーム
python3 dataset_point_collect.py --frames 100  # フレーム数指定
python3 dataset_point_collect.py --mode manual # s で1枚ずつ

# タイムラプス（5分間隔・12時間）
python3 timelapse_detect.py --interval 300 --duration 12.0
# YOLO 検出ログ付き（起動時にモデルを GUI で選択）
python3 timelapse_detect.py --detect
```

### detect/ — YOLO リアルタイム検出

```bash
cd nyx660_script/detect
python3 yolo_detection.py
```

ToF 深度を利用して各検出 BBox に距離（X.XXm）を表示します．モデルは `config.yaml` の `model.yolo_path` で指定（`--model` で上書き可）．

### record/ — MP4 録画

```bash
cd nyx660_script/record

# color + depth colormap の MP4 録画（Enter 開始、q 停止）
python3 mp4_collect.py

# YOLO 検出 + 距離オーバーレイの MP4 録画
python3 record_with_yolo.py
```

### process/ — 後処理

```bash
cd nyx660_script/process

# 点群マージ（省略時は最新セッションを自動選択）
python3 point_merge.py
python3 point_merge.py <session_dir>
python3 point_merge.py <session_dir> --sequential

# タイムラプスログのグラフ生成（analysis.png を出力）
python3 timelapse_analysis.py
python3 timelapse_analysis.py <session_dir>
```

### click_script/ — アノテーション補助

```bash
cd nyx660_script/click_script

# カメラ映像をクリックして座標＋画像を保存（s 保存、q 終了）
python3 click_dataset.py

# 既存画像に YOLO 形式 BBox をアノテーション（d/a で移動、s で保存）
python3 bbox_click.py
```

## テスト

保存処理と模擬カメラによるテストは，カメラを接続せずに実行できます．

```bash
python3 -m unittest discover -s tests -p test_snapshot.py
```
