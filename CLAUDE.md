# CLAUDE.md — hormuz-ship-tracker（cloud セッション用の引き継ぎ）

ホルムズ海峡の船舶監視。AIS（aisstream）の標本収集と、Sentinel-1 SAR からの船影検出を GitHub Actions で回し、
HF dataset `yasumorishima/hormuz-ais` に積んで、GitHub Pages の地図（`docs/`）がブラウザから HF を直接読む。
設計の根拠は `docs/PIPELINE.md` が正。

## 🔴 進め方（2026-09-24 から Claude Code cloud で進める）

- **作業は cloud だけで完結させる**。ユーザーの PC・Raspberry Pi には依存しない（旧作業クローンは RPi5 にあったが使わない）。
- この repo は **public**＝Actions の分数は無制限。収集・統合・公開は Actions の schedule が自走しているので、cloud は**開発と検証**を担う。
- cloud から workflow の dispatch はできない（403）。run の状態は GitHub MCP で読む。変更は作業ブランチ → PR → **自分で merge**。
- merge したら master の run（`tests.yml` と、触った workflow の次回実行）を見るまで「完了」と言わない。
- **ネットワーク**：必要な外部ホストは `planetarycomputer.microsoft.com`（STAC 検索・SAS 署名・匿名）、
  `sentinel1euwest.blob.core.windows.net`（S1 GRD の COG）、`ai4edataeuwest.blob.core.windows.net`（ESA WorldCover・マスク生成だけ）、
  `huggingface.co` 系（読み取りは匿名）。届かなければ環境の許可リストの不足としてユーザーに 1 行で伝える。
- **秘密情報は使わない**。`HF_TOKEN` と aisstream のキーは Actions の secrets にだけある。SAR 側は匿名で取れるので、
  smoke も検証も秘密なしで回す（**本番キーをバグ探しに使わない**）。
- 依存：`pip install -r requirements-test.txt`（SAR の依存も含む・`websockets==15.0.1` と `shapely==2.0.7` は固定のまま緩めない）。
  検査は `tests.yml` と同じ `unittest.defaultTestLoader.discover("tests")`（09-17 時点で 77 件・skip 1 件でも赤扱い）＋ `python scripts/sar_smoke.py`（実シーン 1 枚）。
  `scripts/generate_sar_water_mask.py` はピーク約 4.4GB（コンテナなら回せる。RPi5 では回せなかった）。

## 構成

| 面 | 実体 |
|---|---|
| AIS 収集 | `collect.yml`（15 分ごとの設定・実配信は best-effort）が aisstream に **180 秒だけ**接続 → 陸地フィルタ → HF `raw/<日>/<HHMMSS>.parquet` |
| 統合 | `compact.yml`（日次）が終わった日を `daily/<日>.parquet` へ＋データセットカードを HF へ（`push_card.py`） |
| 公開 | `publish.yml`（3 時間ごと）が HF → 使い捨て SQLite（`positions` のみ）→ 描画 → `docs/` を commit。4 workflow の enable を打ち直す（60 日停止対策） |
| SAR | `sar-collect.yml`（日次 04:41 UTC）＝`src/sar_scene.py`（STAC＋SAS＋GCP 付き COG）→ `src/sar_detect.py`（ブロック中央値＋MAD の背景 → `bg + 10·sd` → 連結成分 → 形状）→ `src/sar_store.py`（HF `sar/det/v1/` と `sar/scenes/v1/` を 1 コミット） |
| 陸マスク | `data/sar_water_mask.tif`＝ESA WorldCover v200 10m（**CC BY 4.0**）。生成器 `scripts/generate_sar_water_mask.py` |
| 地図 | GitHub Pages（master の `/docs`）。サーバーなし。背景は同梱の Natural Earth 陸地（`docs/land_mask.geojson`・`data/` とバイト一致をテストで固定） |

`compact.yml` / `publish.yml` / `sar-collect.yml` は同じ concurrency group `hormuz-hub`。

## 現在地（2026-09-24）

- **AIS は供給元が止まっている**：全世界を 180 秒購読すると 9,500〜12,300 隻受かるのに、海峡の枠は 0（09-16・09-17 に再現）。
  aisstream にペルシャ湾・オマーン湾の受信機が無い。collector の故障ではない。接続成功・位置 0 は warning で success にしてある（赤にしない）。
- **SAR 収集面は稼働中**（PR #10・#11 merge 済・09-24 の日次 run も success）。海峡中心を覆うシーンは約 2 日に 1 枚・遅延 1 日未満。
  検出率＝AIS を正解に **24/24**（300m 以内・Dubai 沖 2 シーン）。**recall だけで precision は未測定**、しかも海峡でなく Dubai 沖で測った。
  初回実収集 1 シーン：候補 2,931・`vessel_sized` 1,633・**うち 48% が海岸 1km 以内**。

## ▶▶ 次にやること

1. `sar-collect.yml` の日次 run が積んだシーン数を HF の `sar/scenes/v1/` で数える（09-17 以降、毎日走っているか・欠けた日は無いか）。
2. `compact.yml` 実行後の HF カードに radar 節が載っているかを実物で確認する。
3. **海岸近傍のクラッタ**：既定の見せ方を決める（`dist_to_land_km` で絞れる形にはしてある）。決める前に距離帯ごとの件数と SNR を測る。
4. **地図に SAR 層を足すか**を、3 を測ってから判断する。足すなら「`vessel_sized` を船と呼ばない」「海岸 1km 以内は混ざる」を画面にも書く。
5. **precision を海峡で測る方法**を考える（AIS が無い今、AIS の無い検出は誤警報とは限らない＝正解の作り方から設計する）。
6. AIS 側は触らない（コスト 0 の待機）。被覆が戻ったら実配信間隔を測る。

## 🔴 主張の前に必ず併記すること

①AIS の窓と窓の間は未観測 ②transit 検知は hosted 側では走っていない（`transit_events` は構造的にゼロ）
③船の静的情報は build 時に同じ MMSI の直近の非空値で埋めている ④**SAR は 2 日に 1 枚の瞬間であって連続航跡ではない**
⑤海岸 1km 以内は地形の倒れ込みと実船が混ざる。⛔ **「1,633 隻」のように検出数を船の数と言わない**。

## ⛔ この形を崩さない

- HF の Docker/Gradio Space は個人アカウントだと有料 → 使わない。無料は Static Space だけ。
- 6 時間 job をループさせて Actions に常駐させない（利用規約）。
- タイル業者に依存しない（CARTO の無キー版は「API KEY REQUIRED」を焼き込む＝実測）。
- **GSHHG は public domain ではなく LGPLv3**。陸マスクは WorldCover（CC BY 4.0・出典併記）。
- HF の split 名は `train` のまま（改名すると既存の `split="train"` 利用が壊れる）。カードの `data_files` は具体パスを先頭に。

## 🔴 踏んだ穴（再発させない）

SAR：
- **`WarpedVRT` に `transform`/`width`/`height` を GCP と一緒に渡すと、定数画像が返って例外も出ない**。GCP 付きの S1 GRD は
  `crs is None` で `transform` が単位行列＝`src.xy()` や `windows.from_bounds()` も画素番号を度として返す。
  正解は ①`src_crs=gcp_crs` だけ渡した自動構成の `WarpedVRT` で窓を読む ②同一 CRS の `reproject` で固定格子へ合わせる、の 2 段。
- **緯度経度格子は正方形でない**（南北 22.26m / 東西 19.92m）。等方の画素長を使うと東西を 12% 過大評価する（出荷コードと検証で設定が食い違っていた前科あり）。
- `GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR` を export したまま `gdal_translate -of ENVI` すると出力 2 バイトで exit 0。/vsicurl 用の設定は読み出しの中だけに閉じる。
- blob 読み出しは一過性に落ちる。`sar_scene.read_scene` が**署名し直して**最大 3 回再試行する（SAS は約 45 分で失効）。
- PC の item id は SAFE 名の末尾 4 桁を落とす。出力パスの主キーは item id。
- 検出器の数字を書くときは**出荷する検出器で測る**（粗い検出器の「533 対 30」を docs に書いた前科）。出荷検出器では外洋で両マスク一致、海岸 1km 以内で差が出る。

AIS・公開：
- `time.monotonic()` は boot からの秒数。「未記録」の番兵を 0 にしない。
- 切断後の再接続は 1 秒から倍々・上限 15 秒・窓の残りでクランプ（固定 1 秒はキーが弾かれたとき 1 日 17,000 回の連打になる）。
  websockets の `open_timeout` 既定 10 秒は窓に縛られない＝`min(10, 残り)` を明示。
- `received_at` は ISO の `T` 入り。SQLite の `datetime()` と比べるときは `strftime('%Y-%m-%dT%H:%M:%f', ...)`。
- 時刻は UTC 明示（`datetime.now(tz=None)` を UTC と刻んでいた前科。CI の smoke はわざと `TZ: Asia/Tokyo` で描画して刺す）。
- `build_sqlite` は `positions` しか作らない＝`transit_events` を無防備に引かない。
- `ship_name`/`destination` は空文字であって null ではない。違う船から静的情報をコピーしない。
- テストの `sys.modules["land_filter"]` stub は discovery 下で後から import した側に漏れる＝差し替えは `ais_parse.is_on_land` を直接。
- 地図：CSP に `'wasm-unsafe-eval'` が要る（`hyparquet-compressors` が WebAssembly）。無いと "Loading" のまま無言で止まる。
  headless ブラウザで確かめるときはページの console を必ず拾う。`mmsi` はブラウザでは BigInt（`JSON.stringify` で落ちる）。
- 図の中にも主張が焼かれている（`heatmap.py` が描いた文言）。文章を直したら画像も直す。
- CodeRabbit は star 10 未満の repo を自動レビューしない（`SUCCESS` の緑はレビュー済みではない）。走らせるときは bot コメントの
  `- [ ] 🔍 Trigger review` を `- [x]` にする。
