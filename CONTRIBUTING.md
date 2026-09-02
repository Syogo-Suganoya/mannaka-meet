# 開発ガイド

マンナカの開発手順。プロダクトの概要とデモの触り方は [README.md](README.md) を参照。

開発環境はすべて Docker に閉じてある。ホストに Python や graphviz を入れる必要はない。

## 開発サイクル

```bash
docker compose up --build     # 起動（app/ web/ はホストとマウント、--reload 有効）
docker compose run --rm api pytest -q   # テスト
docker compose down           # 停止
```

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
    mock/            既定の実装（駅すぱあと・会議室・カレンダー・LLM）
    ekispert/        駅すぱあとAPI（実接続の差し替え先）
    google/          Gemini / Google Calendar
    registry.py      環境変数からアダプタを組み立てる唯一の場所
  agents/
    orchestrator.py  進行管理と状態遷移
    optimizer.py     候補算出・ポリシー適用（自律）
    arranger.py      空き照会・仮押さえ（自律）／確定は承認ゲート
    notifier.py      チャット投稿とアプリ内通知の出し分け（自律）
    follower.py      運行障害の監視と時刻調整の起案（起案まで）
    adk_agent.py     ADK LlmAgent としての公開（Gemini 使用時）
  repositories/    firestore（既定）/ memory（テスト用）
  services/
    audit.py         監査ログ（Firestore + 構造化JSONログ）
    chat.py          自作チャット（依頼の入口・エージェントの返答）
    notifications.py アプリ内通知（本人宛の経路・運賃・承認依頼）
  api/routes.py    REST API
web/               チャット・候補提示・通知のUI
docs/              アーキテクチャ図の定義と出力
tests/             pytest
```

### 設計の約束ごと

- **ドメイン層は `ports/` のインターフェースしか見ない。** モックか実APIかを知っているのは
  `adapters/registry.py` だけ。新しい外部サービスを足すときは、ポートを定義 →
  モック実装 → registry に分岐、の順で進める。
- **承認ゲートを迂回する経路を作らない。** 有料予約の確定は `ArrangerAgent` を通し、
  ADK のツールとしても公開しない（`adk_agent.py`）。
- **判断したことは監査ログに残す。** ポリシー選択・支出承認・リスケの判断根拠は
  `AuditService.record()` の payload に構造化して入れる。
- **チャットと通知の宛先を混ぜない。** チャットは全員が見る場なので集計値だけ。
  個人の出発駅・経路・運賃、主催者への承認依頼は Notification で本人にだけ届ける。
  通知はアプリ内に保存するだけで、外部へは送らない。

## モック → 実APIの差し替え

すべて環境変数で切り替わる。アダプタの初期化に失敗した場合はモックに落ちて起動を継続し、
どちらで動いているかは `/healthz` と画面上部のバッジに出る。

| 変数 | 既定 | 実APIにするとき |
|---|---|---|
| `TRANSIT_PROVIDER` | `mock` | `ekispert` ＋ `EKISPERT_API_KEY` |
| `CALENDAR_PROVIDER` | `mock` | `google`（Cloud Run のサービスアカウントで ADC） |
| `LLM_PROVIDER` | `stub` | `gemini` ＋ `GOOGLE_API_KEY` |
| `GEMINI_MODEL` | `gemini-3.7-flash` | 使用するGeminiモデル |
| `ROOMS_PROVIDER` | `mock` | （MVP対象外。設計書 §8） |
| `AGENT_SPEND_LIMIT_YEN` | `5000` | エージェントの支出上限 |

例:

```bash
TRANSIT_PROVIDER=ekispert EKISPERT_API_KEY=xxxxx docker compose up
```

差し替え時に触るのは対応するアダプタ1ファイルだけ。ドメイン層・エージェント・
API・UI・テストはいずれも変更不要。

設定項目の一覧は [.env.example](.env.example) にある。

## データストア

データはすべて Firestore に置く（設計書 §6）。`docker compose up` で Firestore
エミュレータが一緒に立ち上がり、api はそれが healthy になってから起動する。
設定は不要で、`/healthz` の `repository` が `firestore` になっていれば繋がっている。

| コレクション | 中身 |
|---|---|
| `users` | 参加者プロフィール（最寄り駅まで） |
| `meetings` | 依頼・候補・決定・経路・配信・起案 |
| `audit` | ポリシー選択・支出承認・リスケの証跡 |
| `messages` | 自作チャットの発言 |
| `notifications` | アプリ内通知 |

**エミュレータはデータをメモリに持つ。** api を再起動してもデータは残るが、
エミュレータのコンテナを作り直すと消える。消したいときは `docker compose down`。

クエリは単一フィールドの等価比較だけにし、並べ替えと絞り込みの残りは Python 側で
行っている。本番 Firestore で複合インデックスの作成を必要としないためで、
リポジトリを触るときはこの方針を崩さないこと。

インメモリ実装（`REPOSITORY=memory`）はテスト専用に残してある。

### モックの作り

- **経路・運賃**: 首都圏20駅の粗い座標グラフから決定的に算出（乱数なし）。
  都心ターミナルは速いが運賃水準が高く、郊外の結節点は遅いが安い。この非相関により、
  3つのポリシーで結論が割れる。
- **会議室**: 5テンプレート × 決定的な空き状況。自社拠点は無料 → 承認不要の分岐を再現。
- **運行実況**: `POST /api/demo/disruption` で任意の路線に遅延を注入できる。

モックは乱数を使わない。同じ入力なら常に同じ出力になるので、テストとデモの再現性が保てる。

## テスト

```bash
docker compose run --rm api pytest -q
docker compose run --rm api pytest tests/test_flow.py -v   # 個別に流す
```

| ファイル | 対象 |
|---|---|
| `tests/test_optimization.py` | 3つの評価関数、匿名化 |
| `tests/test_parser.py` | 日本語の依頼文の解釈（LLMなし） |
| `tests/test_flow.py` | 一気通貫と承認ゲート、当日フォロー、監査ログ |
| `tests/test_api.py` | REST API とエラーコード |
| `tests/test_firestore_repository.py` | Firestore への実際の読み書き（エミュレータ） |

`tests/conftest.py` の `container` フィクスチャで全アダプタをモックに、ストアを
インメモリに固定している。外部ネットワークには出ない。

`test_firestore_repository.py` だけはエミュレータに実際に読み書きする。
`FIRESTORE_EMULATOR_HOST` が無い環境では自動でスキップされる。

ガバナンスの振る舞い（承認者チェック・支出上限・匿名化・起案どまり）は仕様なので、
変更するときはテストを先に直してから実装に触る。

## API

| メソッド | パス | 用途 |
|---|---|---|
| GET | `/api/chat/messages` | チャットの履歴 |
| POST | `/api/chat/messages` | チャットに発言（`@マンナカ` で依頼として解釈） |
| GET | `/api/notifications?uid=` | 本人宛のアプリ内通知 |
| POST | `/api/notifications/{id}/read` | 通知を既読にする |
| POST | `/api/users` | 参加者登録（最寄り駅のみ） |
| POST | `/api/meetings` | 依頼文の解釈 |
| POST | `/api/meetings/{id}/optimize` | ポリシー指定で候補算出 |
| GET | `/api/meetings/{id}/policy-comparison` | 3ポリシーの結論の差 |
| GET | `/api/meetings/{id}/rooms?station=` | 会議室の空き照会 |
| POST | `/api/meetings/{id}/arrange` | 仮押さえ＋承認要求 |
| POST | `/api/meetings/{id}/approval` | 主催者の支出承認 |
| POST | `/api/meetings/{id}/confirm` | 確定＋カレンダー＋個別通知 |
| POST | `/api/meetings/{id}/follow-up` | 遅延監視と時刻調整の起案 |
| POST | `/api/meetings/{id}/proposal` | 起案の承諾／見送り |
| GET | `/api/meetings/{id}/audit` | 監査ログ |

エラーコードにガバナンスが表れる: `403` 主催者以外の承認、`402` 支出上限超過、
`428` 未承認のまま確定しようとした。

起動中は <http://localhost:8081/docs> に OpenAPI ドキュメントが出る。

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
