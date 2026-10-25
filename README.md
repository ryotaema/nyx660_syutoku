# nyx660_syutoku

Vzense NYX660（3D ToF + RGB カメラ）を用いた学習データ収集スクリプト群です．
RGB・深度・IR・点群の撮影，録画，タイムラプス，YOLO 検出，アノテーション補助を GUI からボタンで実行できます．

## セットアップ

ROS は不要です．Python とメーカー SDK でカメラへ直接接続します．

1. パッケージを入れる

   ```bash
   pip install opencv-python numpy pyyaml
   # 必要に応じて: ultralytics（YOLO）/ open3d（点群マージ）/ matplotlib（タイムラプスのグラフ）
   ```

2. [ScepterSDK](https://github.com/ScepterSW/ScepterSDK)（**v26.03 以降**）をこのリポジトリと同じ階層に clone する

   ```
   <作業フォルダ>/
   ├── nyx660_syutoku/   # このリポジトリ
   └── ScepterSDK/       # git clone https://github.com/ScepterSW/ScepterSDK.git
   ```

3. PC の有線 LAN を `192.168.1.100/24` に設定する（カメラの既定 IP は `192.168.1.101`．ギガビット接続が必要）

SDK を別の場所に置く場合・環境ごとの設定（`config.local.yaml`）・Docker で使う場合は [docs/setup.md](docs/setup.md) を参照してください．

## 使い方

```bash
python3 nyx660_script/gui.py
```

1. 「カメラ」に NYX660 が表示されることを確認します（出ないときは LAN を確認して「再検出」）．
2. タブ（画像・点群・動画・タイムラプス・推論・アノテーション）を開いてボタンを押します．
3. 開いたウィンドウで，ボタン横の「操作」に書かれたキーで保存・終了します．途中でやめるときは「停止」．

保存先は `data/` 配下です（「保存先を開く」で開けます）．各スクリプトはコマンドラインからも実行できます．

## ドキュメント

| 資料 | 内容 |
|---|---|
| [docs/setup.md](docs/setup.md) | セットアップ詳細（SDK の配置，ネットワーク，`config.yaml` / `config.local.yaml`，Docker） |
| [docs/usage.md](docs/usage.md) | GUI の詳細，コマンドラインでの使い方，CLI オプション一覧 |
| [docs/data_format.md](docs/data_format.md) | ファイルの命名規則，保存されるフォルダ・ファイルの形式，カメラ仕様 |
| [docs/legacy_conversion.md](docs/legacy_conversion.md) | 旧形式データ（点群の単位・旧ファイル名）の変換 |
| [RealSense_vs_NYX660.md](RealSense_vs_NYX660.md) | RealSense との比較 |

## ライセンス

このリポジトリは [GNU Affero General Public License v3.0 (AGPL-3.0)](LICENSE) のもとで公開されています。

本プロジェクトは [Ultralytics YOLO](https://github.com/ultralytics/ultralytics)（AGPL-3.0）を使用しているため、AGPL-3.0 に従い同ライセンスを適用しています。

### サードパーティライセンス

本プロジェクトは以下のライブラリを使用しています。

| ライブラリ | ライセンス |
|---|---|
| [ScepterSDK](https://github.com/ScepterSW/ScepterSDK) | BSD 3-Clause（Copyright © 2024 Scepter Software） |
| [Ultralytics YOLO](https://github.com/ultralytics/ultralytics) | AGPL-3.0 |
| [Open3D](https://github.com/isl-org/Open3D) | MIT |
| [OpenCV](https://github.com/opencv/opencv) | Apache 2.0 |
| [NumPy](https://github.com/numpy/numpy) | BSD 3-Clause |
| [PyYAML](https://github.com/yaml/pyyaml) | MIT |
| [Matplotlib](https://github.com/matplotlib/matplotlib) | PSF-based（BSD 互換） |
