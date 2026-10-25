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

## 点群に色を付ける

2026-10-25 より前の PLY は座標（x, y, z）だけで色を持たず，CloudCompare などでは灰色で表示されます．
同じ連番のカラー画像（`color_raw/*_cr.png`，無ければ `*_c.jpg`）とセッションの `intrinsics.json` から色を付けます．座標は変わりません．
mm 単位の旧形式の PLY は，先に上の「旧形式の点群 PLY の変換」をしてください．

GUI では「ツール」タブ →「点群に色を付ける」でフォルダを選びます．コマンドラインでは次のとおりです．

```bash
# 何に色が付くか確認（dry-run）
python3 tools/colorize_ply.py data

# 実行
python3 tools/colorize_ply.py data --apply
```

マージ結果（`*_merged_pc.ply`）は対象外です．色を付けた後に `point_merge.py` をやり直すと，マージ結果にも色が付きます．

## 点群の歪みを補正

カメラの SDK が作る点群は ToF レンズの歪みを補正しておらず，撮影スクリプトはその値のまま PLY に保存します．
画面の中心はほぼ正しいですが，端ほど横にずれます（補正した位置との差は中央値 約2cm，画面の端の遠い点で最大 約20cm）．
形の正確さが必要なときは，このツールで座標を直します．深度（カメラからの距離）と色は変わりません．

- SDK の値のままかどうかは点の並びから自動で判定するので，補正済みの PLY はスキップされます（二重に補正されません）．
- セッションの `intrinsics.json` が必要です．mm 単位の旧形式は先に「旧形式の点群 PLY の変換」をしてください．
- 補正前のファイルは `*.ply.raw_bak` として残ります（`--no-backup` で残さない）．
- 補正した後は，`point_merge.py` をやり直すとマージ結果にも反映されます．

GUI では「ツール」タブ →「点群の歪みを補正」でフォルダを選びます．コマンドラインでは次のとおりです．

```bash
# 何が補正されるか確認（dry-run）
python3 tools/undistort_ply.py data

# 実行
python3 tools/undistort_ply.py data --apply
```

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
