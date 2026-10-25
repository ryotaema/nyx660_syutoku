# 旧データの変換

以前の形式で保存したデータを，現在の形式（[data_format.md](data_format.md)）に揃えるためのツールです．

## 旧形式の点群 PLY の変換

2026年10月より前の PLY（mm・SDK 座標系）を，現在の形式（m・RealSense Viewer 座標系）に変換します．
`metadata.json` の `ply_format` と点の z の値から旧形式かどうかを判定し，変換済みのものは自動でスキップします．

```bash
# 何が変換されるか確認（dry-run）
python3 tools/convert_ply_to_m.py data

# 実行（変換前のファイルは *.ply.mm_bak として残る。不要なら --no-backup）
python3 tools/convert_ply_to_m.py data --apply
```

マージ結果（`*_merged_pc.ply`）は変換しないので，変換後に `point_merge.py` をやり直してください．

## 旧命名データの変換

2026年7月より前に取得した旧命名（`imageN_YYYY-MM-DD_HHMMSS_NYX660/` など）のデータは，
そのまま置いておけます．新命名に揃えたい場合は変換ツールを使ってください．

旧ファイル名のモダリティ接尾辞（`_color` / `_depth_colormap` など）を手がかりに，
同一ショットのファイルへ同じ連番を振り直します．`labels/*.txt` や `*.xml` も追従します．
既定はコピーなので原データは残ります．

### GUI（推奨）

フォルダを選んで，変換後のファイル名を一覧で確認してから実行できます．
データが複数の場所に散らばっている場合も，変換元フォルダをいくつでも登録できます．

```bash
python3 tools/rename_legacy_gui.py
```

1. **変換元フォルダ** — 「フォルダを追加...」で登録（複数可）．
   データルート（`data/images`）を指定すれば配下のセッションをまとめて拾います．
2. **設定** — カメラコードは通常「自動判別」のままで構いません．フォルダ名から
   判別できないデータ（`click_test_data` など）はスキップ理由が表示されるので，
   そのときだけ `nyx` を指定します．タグ・出力先・コピー/移動もここで指定．
3. **プレビューを作成** — セッションごとに「変換前 → 変換後」が並びます．
   行を開くと個々のファイル名を確認できます．
4. **変換を実行** — プレビューを作るまで実行ボタンは押せません．

### CLI

```bash
# 何が起きるか確認（dry-run。既定ではファイルを書き換えません）
python3 tools/rename_legacy.py data/images

# 実行（コピーで出力するため原データは残ります）
python3 tools/rename_legacy.py data/images --apply

# ディレクトリ名からカメラ・日時が分からないデータ
python3 tools/rename_legacy.py data/click_test_data/250911_testdata_click --cam nyx --apply
```

`--move` を付けると移動になります（原データが残らないので注意）．
