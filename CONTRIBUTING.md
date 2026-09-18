# 開発ガイド

マンナカの開発手順。プロダクトの概要と使い方は [README.md](README.md) を参照。

開発環境はすべて Docker に閉じてある。ホストに Python や graphviz を入れる必要はない。

## 開発サイクル

```bash
docker compose up --build     # 起動（app/ web/ はホストとマウント、--reload 有効）
docker compose run --rm api pytest -q   # テスト
docker compose down           # 停止

# トップページに載せる画面の写しを撮り直す（web/shots/*.png）
# 数字が毎回変わらないよう、モックのまま撮る
TRANSIT_PROVIDER=mock LLM_PROVIDER=stub \
  docker compose --profile shots up --build shots
docker compose --profile shots down   # 撮ったあとはこちらで止める
```

画面を変えたら `web/shots/*.png` も撮り直してコミットする。トップページの「使い方」は
この写しをそのまま載せているので、撮り直さないと説明と画面が食い違う。

`app/` と `web/` はコンテナにマウントしているので、編集すればそのまま反映される。
依存を増やしたときだけ `--build` を付け直す。

ホスト側のポートは `HOST_PORT` で変えられる（既定 8081）。

## ディレクトリ構成

```
app/
  domain/          モデルと評価関数（外部依存なし）
    models.py        Firestore のデータモデルに対応
    optimization.py  sum / minimax / cost の評価関数
  ports/           外部サービスの抽象インターフェース
  adapters/
    mock/            既定の実装（駅すぱあと・LLM）
    ekispert/        駅すぱあとAPI（実接続の差し替え先）
    google/          Gemini
    registry.py      環境変数からアダプタを組み立てる唯一の場所
  agents/
    orchestrator.py  依頼の受付と進行管理
    optimizer.py     候補算出・ポリシー適用（自律）
    adk_agent.py     ADK LlmAgent としての公開（Gemini 使用時）
  repositories/    firestore（既定）/ memory（テスト用）
  services/
    audit.py         監査ログ（Firestore + 構造化JSONログ）
  api/routes.py    REST API
web/
  index.html       トップページ（機能と使い方）→ `/`
  landing.css      トップページ専用スタイル
  app.html         アプリ本体（入力フォーム・表情・候補地）→ `/app`
  app.js           アプリの挙動
  app.css          アプリのスタイル
  shots/           トップページに載せる画面の写し（docs/shots.js が撮る）
docs/              アーキテクチャ図の定義と出力
  shots.js         画面の写しを撮る手順
tests/
  factories.py     テスト用の固定参加者6名
```

### 設計の約束ごと

- **ドメイン層は `ports/` のインターフェースしか見ない。** モックか実APIかを知っているのは
  `adapters/registry.py` だけ。新しい外部サービスを足すときは、ポートを定義 →
  モック実装 → registry に分岐、の順で進める。
- **どこに集まるかは決めない。** 候補と根拠を出すところまでが仕事。
  場所を選ぶツールは ADK にも公開しない（`adk_agent.py`）。
- **判断したことは監査ログに残す。** ポリシー選択と評価の根拠は
  `AuditService.record()` の payload に構造化して入れる。
- **個人情報を増やさない。** 受け取るのは名前と出発駅まで。住所・連絡先・
  アカウントを足したくなったら、それが本当に比較に要るのかを先に問う。

## モック → 実APIの差し替え

すべて環境変数で切り替わる。アダプタの初期化に失敗した場合はモックに落ちて起動を継続し、
どちらで動いているかは `/health` と画面上部のバッジに出る。

| 変数 | 既定 | 実APIにするとき |
|---|---|---|
| `TRANSIT_PROVIDER` | `mock` | `ekispert` ＋ `EKISPERT_API_KEY` |
| `LLM_PROVIDER` | `stub` | `gemini` ＋ `GOOGLE_API_KEY` |
| `GEMINI_MODEL` | `gemini-3.7-flash` | 使用するGeminiモデル |

例:

```bash
TRANSIT_PROVIDER=ekispert EKISPERT_API_KEY=xxxxx docker compose up
```

差し替え時に触るのは対応するアダプタ1ファイルだけ。ドメイン層・エージェント・
API・UI・テストはいずれも変更不要。

設定項目の一覧は [.env.example](.env.example) にある。

## データストア

データはすべて Firestore に置く（設計書 §7）。`docker compose up` で Firestore
エミュレータが一緒に立ち上がり、api はそれが healthy になってから起動する。
設定は不要で、`/health` の `repository` が `firestore` になっていれば繋がっている。

| コレクション | 中身 |
|---|---|
| `meetings` | 依頼（日時・参加者）・採用した基準・候補と内訳 |
| `audit` | どの基準でどう評価したかの証跡 |

参加者はリクエストごとの連番 `uid` で持ち、コレクションにはしない。
ログインが無いので、会をまたいで同じ人を指す識別子が存在しないため。

**エミュレータはデータをメモリに持つ。** api を再起動してもデータは残るが、
エミュレータのコンテナを作り直すと消える。消したいときは `docker compose down`。

クエリは単一フィールドの等価比較だけにし、並べ替えと絞り込みの残りは Python 側で
行っている。本番 Firestore で複合インデックスの作成を必要としないためで、
リポジトリを触るときはこの方針を崩さないこと。

インメモリ実装（`REPOSITORY=memory`）はテスト専用に残してある。

### モックの作り

- **経路・運賃**: 首都圏20駅の粗い座標グラフから決定的に算出（乱数なし）。
  ここに無い駅は `TransitPort.known_stations()` で弾く。黙って近くのハブに寄せると
  その人だけ0分0円になり、どの候補が最良かという結論そのものが狂うため。
  都心ターミナルは速いが運賃水準が高く、郊外の結節点は遅いが安い。この非相関により、
  3つのポリシーで結論が割れる。
- **駅の座標**: 同じ表の実座標（緯度・経度）を返す。地図を描くためだけに使い、
  評価には入れない。所要と運賃は上の相対kmから出す。

モックは乱数を使わない。同じ入力なら常に同じ出力になるので、テストと動作確認の再現性が保てる。

## テスト

```bash
docker compose run --rm api pytest -q
docker compose run --rm api pytest tests/test_flow.py -v   # 個別に流す
```

| ファイル | 対象 |
|---|---|
| `tests/test_optimization.py` | 3つの評価関数、内訳の並び |
| `tests/test_parser.py` | 日本語の依頼文の解釈（LLMなし） |
| `tests/test_flow.py` | 入力フォーム、候補算出、内訳、監査ログ |
| `tests/test_api.py` | REST API とエラーコード |
| `tests/test_firestore_repository.py` | Firestore への実際の読み書き（エミュレータ） |

`tests/conftest.py` の `container` フィクスチャで全アダプタをモックに、ストアを
インメモリに固定している。外部ネットワークには出ない。

`test_firestore_repository.py` だけはエミュレータに実際に読み書きする。
`FIRESTORE_EMULATOR_HOST` が無い環境では自動でスキップされる。

ガバナンスの振る舞い（未知駅の拒否・基準の記録・場所を決めないこと）は仕様なので、
変更するときはテストを先に直してから実装に触る。

## API

| メソッド | パス | 用途 |
|---|---|---|
| POST | `/api/meetings` | 依頼フォームから作成（候補算出まで） |
| GET | `/api/meetings/{id}` | 会議の取得 |
| POST | `/api/meetings/{id}/optimize` | ポリシー指定で候補算出 |
| GET | `/api/meetings/{id}/policy-comparison` | 3ポリシーの結論の差 |
| GET | `/api/meetings/{id}/audit` | 監査ログ |
| GET | `/health` | 稼働確認とプロバイダの表示 |

`400` は経路を引けない駅や空の出発駅、`404` は無い会議、`422` は参加者ゼロ。

`/healthz` にはしない。Cloud Run の手前の Google フロントエンドがこのパスを
横取りし、コンテナに届く前に404を返す（`app/main.py` に理由を残してある）。

起動中は <http://localhost:8081/docs> に OpenAPI ドキュメントが出る。

画面のルートは `app/main.py` の末尾で定義している（`/` がトップ、`/app` がアプリ本体）。

## アーキテクチャ図

```bash
docker compose --profile docs run --rm docs
```

`docs/mannaka_architecture.png` を再生成する。定義は [docs/architecture.py](docs/architecture.py)。
graphviz と日本語フォントは `Dockerfile.docs` に入れてあるので、ホストへの導入は不要。

自動生成ではなく手書きの定義なので、構成を変えたときは合わせて更新する。

## デプロイ

Cloud Run への手順は [DEPLOY.md](DEPLOY.md) にまとめてある
（CLI・画面操作・GitHub Actions の3パターン）。

GitHub Actions の CD は `.github/workflows/mannaka-meet-cd.yml`。**現在オフ**で、
手動実行するとテストだけ流れる。オンにするには GitHub の Variables に
`CD_ENABLED=true` を設定する（DEPLOY.md パターンC）。

APIキーはリポジトリにコミットしない。ローカルで実APIを試すときは `.env`
（`.gitignore` 対象）に置き、Cloud Run では Secret Manager 経由で渡す。
