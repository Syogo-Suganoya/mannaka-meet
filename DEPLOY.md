# デプロイ手順

マンナカを Cloud Run にデプロイする。**CLI（gcloud）**・**画面操作（Cloud Console）**・
**GitHub Actions（CD）** の3パターンを載せる。どれでも結果は同じ。

> **未検証の注意**: 以下は Google Cloud の公式ドキュメントに沿って書いたもので、
> このリポジトリで実際にデプロイして確認したものではない。初回は表示される
> エラーメッセージを見ながら進めること。コンソールのボタン名は変わることがある。

| | A: CLI | B: 画面操作 | C: GitHub Actions |
|---|---|---|---|
| ローカルのコードをそのまま上げる | できる（`--source .`） | **できない** | — |
| 初回の所要 | 5〜10分 | 15〜25分 | 20〜30分（WIF設定） |
| 2回目以降 | コマンド1つ | 画面操作 | **push だけ** |
| 向いている場面 | ハッカソン当日、手早く上げたい | GUIで設定を確認したい | 継続的に更新する |

初回は **A: CLI** が速い。画面操作はローカルフォルダのアップロードに対応していないため、
GitHub にリポジトリを置くか、先にコンテナイメージを push する手間が増える。
**C は現在オフ**にしてある（設定手順はパターンCを参照）。

---

## 事前準備（3パターン共通）

### 決めておくもの

以下の値を前提に手順を書いてある。プロジェクトIDが違う場合は読み替えること。

| 項目 | 値 |
|---|---|
| プロジェクトID | `mannaka-meet` |
| リージョン | `asia-northeast1`（東京） |
| サービス名 | `mannaka-meet` |

### 有効化するAPI

- Cloud Run Admin API
- Cloud Build API（ソースからビルドするため）
- Artifact Registry API（ビルド済みイメージの保管先）
- Firestore API（**必須**。データの置き場所）
- Secret Manager API（実APIキーを使う場合）
- Generative Language API（Gemini を使う場合）

### 動作モードを決める

外部APIは**モックのままでもデプロイして動く**。まずモックで上げて、動作確認して
から実APIに切り替えるのが安全。ただし**Firestore は必須**（データの置き場所なので）。

| モード | 環境変数 | 追加で要るもの |
|---|---|---|
| 外部APIはモック（最小構成） | 既定値のまま | **Firestore DB、IAM権限** |
| Gemini も使う | ＋ `LLM_PROVIDER=gemini` | APIキー（Secret Manager） |
| 駅すぱあとも実接続 | ＋ `TRANSIT_PROVIDER=ekispert` | APIキー（Secret Manager） |

環境変数の一覧は [.env.example](.env.example) と
[CONTRIBUTING.md](CONTRIBUTING.md#モック--実apiの差し替え) を参照。

---

## パターンA: CLI（gcloud）

### A-1. 初期設定

```bash
gcloud auth login
gcloud config set project mannaka-meet
gcloud config set run/region asia-northeast1
```

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com
```

### A-2. Firestore を用意する

アプリより先に作っておく。データの置き場所なので、無いと起動しても保存できない。

```bash
gcloud services enable firestore.googleapis.com
gcloud firestore databases create --location=asia-northeast1
```

### A-3. デプロイする

リポジトリの `gcp_hack/mannaka-meet` ディレクトリで実行する。

```bash
gcloud run deploy mannaka-meet --source . --allow-unauthenticated \
  --set-env-vars GOOGLE_CLOUD_PROJECT=mannaka-meet
```

- カレントの `Dockerfile` を Cloud Build が使ってビルドする（buildpacks は使われない）
- ビルド済みイメージは Artifact Registry の `cloud-run-source-deploy`
  リポジトリに自動で作られて保管される
- 初回は「Artifact Registry リポジトリを作るか」と聞かれるので `Y`
- コンテナは `$PORT` を見て待ち受ける（[Dockerfile](Dockerfile) の `CMD`）。
  Cloud Run が渡す 8080 でそのまま動く

完了すると `https://mannaka-meet-xxxxxxxx-an.a.run.app` のようなURLが出る。
末尾に `/healthz` を付けて開き、どのプロバイダで動いているか確認する。

```json
{"status":"ok","providers":{"transit":"mock","llm":"stub","repository":"firestore"}}
```

`repository` が `memory` になっていたら、Firestore への接続に失敗して揮発ストアに
落ちている（起動は続く仕様）。次の IAM 設定が抜けていることが多い。

### A-4. Firestore への権限を付ける

Cloud Run のサービスアカウント（既定では
`PROJECT_NUMBER-compute@developer.gserviceaccount.com`）に読み書き権限を付ける。

```bash
PROJECT_NUMBER=$(gcloud projects describe mannaka-meet --format='value(projectNumber)')

gcloud projects add-iam-policy-binding mannaka-meet \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/datastore.user"
```

### A-5. 実APIキーを渡す（Secret Manager）

APIキーは環境変数に直接書かず、Secret Manager 経由で渡す。

```bash
gcloud services enable secretmanager.googleapis.com

printf 'YOUR_GEMINI_KEY' | gcloud secrets create gemini-key --data-file=-
printf 'YOUR_EKISPERT_KEY' | gcloud secrets create ekispert-key --data-file=-
```

サービスアカウントにシークレットの読み取り権限を付ける。

```bash
for SECRET in gemini-key ekispert-key; do
  gcloud secrets add-iam-policy-binding $SECRET \
    --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"
done
```

```bash
gcloud run services update mannaka-meet \
  --set-secrets GOOGLE_API_KEY=gemini-key:latest,EKISPERT_API_KEY=ekispert-key:latest \
  --set-env-vars LLM_PROVIDER=gemini,TRANSIT_PROVIDER=ekispert
```

再び `/healthz` を開き、`"llm":"gemini"` `"transit":"ekispert"` になっていれば成功。
**モックのままなら、キーの設定か権限付与が効いていない**（アダプタの初期化に失敗すると
モックに落ちて起動を継続する仕様のため、502にはならず静かにモックで動く）。
原因はログに出る。

### A-6. 更新・確認・削除

```bash
gcloud run deploy mannaka-meet --source .          # 再デプロイ
gcloud run services describe mannaka-meet          # 現在の設定
gcloud beta run services logs read mannaka-meet --limit 50   # ログ
gcloud run services delete mannaka-meet            # 削除
```

監査ログは構造化JSONで stdout に出しているので、Cloud Logging で
`jsonPayload.component="mannaka.audit"` で絞り込める。

---

## パターンB: 画面操作（Cloud Console）

コンソールからは**ローカルフォルダを直接アップロードできない**。次のどちらかを選ぶ。

- **B-1. GitHub リポジトリと連携する**（継続デプロイも一緒に組める・おすすめ）
- **B-2. コンテナイメージを先に push しておく**（push 自体にはCLIが要る）

### B-0. APIの有効化

1. コンソール上部の検索窓に「API とサービス」と入力して開く
2. **「+ APIとサービスの有効化」** をクリック
3. 次を1つずつ検索して **「有効にする」**
   - Cloud Run Admin API
   - Cloud Build API
   - Artifact Registry API
   - Firestore API（必須）
   - Secret Manager API（使う場合）

### B-1. GitHub リポジトリから継続デプロイ

1. ナビゲーションメニューから **Cloud Run** を開く
2. **「リポジトリを接続」**（Connect repository）をクリック
   - 既存サービスに後から付ける場合は、サービスを開いて **「リポジトリに接続」**
3. ビルド方法の選択で **Cloud Build** を選ぶ
4. **リポジトリの選択**
   - 一覧からリポジトリを選ぶ。初回は **「認証」**（Authenticate）で GitHub の
     アクセスを許可する
   - **「次へ」**
5. **ビルド構成**
   | 項目 | 設定値 |
   |---|---|
   | ブランチ | `^main$` |
   | ビルドタイプ | **Dockerfile** |
   | ソースの場所 | `/gcp_hack/mannaka-meet/Dockerfile` |

   リポジトリ直下にマンナカを置いている場合は `/Dockerfile`。
   Buildpacks ではなく **Dockerfile を選ぶ**こと（このプロジェクトは Dockerfile を同梱している）。
6. **「保存」**
7. サービス作成フォームに戻るので、続けて設定する
   | 項目 | 設定値 |
   |---|---|
   | サービス名 | `mannaka-meet` |
   | リージョン | `asia-northeast1（東京）` |
   | 認証 | **未認証の呼び出しを許可** |
   | コンテナポート | `8080` |
8. **「コンテナ、ボリューム、ネットワーキング、セキュリティ」** を展開し、
   **「変数とシークレット」** タブで環境変数を追加する（下表）
9. **「作成」**

以降は `main` に push するたびに自動でビルド・デプロイされる。

### B-2. コンテナイメージからデプロイ

イメージの push だけは CLI で行う（コンソールにイメージのアップロード機能はない）。

```bash
gcloud auth configure-docker asia-northeast1-docker.pkg.dev
gcloud artifacts repositories create mannaka-meet \
  --repository-format=docker --location=asia-northeast1

docker build -t asia-northeast1-docker.pkg.dev/mannaka-meet/mannaka-meet/app:v1 .
docker push asia-northeast1-docker.pkg.dev/mannaka-meet/mannaka-meet/app:v1
```

1. **Cloud Run** → **「サービスをデプロイ」**（Deploy container）
2. **「既存のコンテナイメージから1つのリビジョンをデプロイする」** を選ぶ
3. **「選択」** から、いま push したイメージを選ぶ
4. 以降は B-1 の手順7以降と同じ

### 画面から設定する環境変数

**「変数とシークレット」** タブで設定する。

**環境変数**（「変数を追加」）

| 名前 | 値 |
|---|---|
| `GOOGLE_CLOUD_PROJECT` | `mannaka-meet` |
| `LLM_PROVIDER` | `gemini` |
| `TRANSIT_PROVIDER` | `ekispert` |

**シークレット**（「シークレットを参照」）

| 環境変数名 | シークレット | バージョン |
|---|---|---|
| `GOOGLE_API_KEY` | `gemini-key` | `latest` |
| `EKISPERT_API_KEY` | `ekispert-key` | `latest` |

シークレットは事前に **Secret Manager** の画面で作る。
「シークレットを作成」→ 名前と値を入れて保存。

### 画面から Firestore を用意する（必須・サービス作成より先に）

1. ナビゲーションメニューから **Firestore**
2. **「データベースを作成」**
3. モードは **Native モード**、ロケーションは `asia-northeast1`
4. **IAM と管理** → Cloud Run のサービスアカウントに
   **Cloud Datastore ユーザー**（`roles/datastore.user`）ロールを追加

---

## パターンC: GitHub Actions（CD）

ワークフローは [`.github/workflows/mannaka-meet-cd.yml`](../../.github/workflows/mannaka-meet-cd.yml)
（リポジトリのルート）。テストを流してから Cloud Run にデプロイする。

> **現在オフ**。push では起動しない。手動実行はできるが、変数 `CD_ENABLED` が
> `true` でなければデプロイ手前で止まり、テストだけ流して終わる。

| ジョブ | 内容 | 現在 |
|---|---|---|
| `test` | pytest（Firestore実接続テストはエミュレータが無いので自動スキップ） | 手動実行時に動く |
| `deploy` | `gcloud run deploy --source .` → `/healthz` で起動確認 | **止まる** |

### C-1. オンにする準備（初回のみ）

GitHub Actions から Google Cloud に入るため、Workload Identity Federation を設定する。
サービスアカウントキーの JSON をリポジトリに置く必要はない。

```bash
gcloud services enable iamcredentials.googleapis.com

# デプロイ用サービスアカウント
gcloud iam service-accounts create github-deployer --project mannaka-meet

for ROLE in roles/run.admin roles/cloudbuild.builds.editor \
            roles/artifactregistry.admin roles/iam.serviceAccountUser \
            roles/storage.admin; do
  gcloud projects add-iam-policy-binding mannaka-meet \
    --member="serviceAccount:github-deployer@mannaka-meet.iam.gserviceaccount.com" \
    --role="$ROLE"
done
```

```bash
# Workload Identity プール／プロバイダ
gcloud iam workload-identity-pools create github --location=global --project mannaka-meet

gcloud iam workload-identity-pools providers create-oidc github \
  --location=global --workload-identity-pool=github --project mannaka-meet \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository=='Syogo-Suganoya/gemini-ops-orchestrator'"
```

```bash
# このリポジトリからの認証だけを許可する
PROJECT_NUMBER=$(gcloud projects describe mannaka-meet --format='value(projectNumber)')

gcloud iam service-accounts add-iam-policy-binding \
  github-deployer@mannaka-meet.iam.gserviceaccount.com --project mannaka-meet \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/attribute.repository/Syogo-Suganoya/gemini-ops-orchestrator"
```

`attribute-condition` でリポジトリを限定している。これが無いと、他人のリポジトリからも
このサービスアカウントを借りられてしまう。

### C-2. GitHub 側の設定

**Settings > Secrets and variables > Actions > Variables** に登録する。
いずれも秘密情報ではないので Secrets ではなく Variables でよい。

| 変数 | 値 |
|---|---|
| `CD_ENABLED` | `true`（これがオン／オフのスイッチ） |
| `WIF_PROVIDER` | `projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github/providers/github` |
| `DEPLOY_SERVICE_ACCOUNT` | `github-deployer@mannaka-meet.iam.gserviceaccount.com` |

### C-3. 実行する

- **手動**: Actions タブ → 「マンナカ CD」→ **Run workflow** →
  「デプロイまで実行する」を **true** にして実行
- **push で自動**: ワークフロー冒頭の `push:` のコメントを外す。
  `gcp_hack/mannaka-meet/**` が変わった push でだけ動く

### C-4. オフに戻す

`CD_ENABLED` を `false` にする（または変数を削除する）。これでワークフローは
テストまでで止まる。push トリガーを付けていた場合はコメントに戻す。

---

## デプロイ後の確認

| 見るところ | 期待する結果 |
|---|---|
| `/healthz` | `providers` が意図したモードになっている |
| `/` | トップページ（機能と使い方）が開く |
| `/app` | 入力フォームが出て、参加者を入れると候補地が並ぶ |
| `/docs` | OpenAPI ドキュメントが出る |
| Cloud Logging | `mannaka 起動: providers=...` が出ている |

## つまずきやすいところ

| 症状 | 原因と対処 |
|---|---|
| 実APIにしたのに `/healthz` が `mock` のまま | アダプタの初期化に失敗してモックに落ちている。ログの警告を見る。キーの値かIAM権限を疑う |
| Firestore で 403 | サービスアカウントに `roles/datastore.user` が付いていない |
| デプロイは成功するがコンテナが起動しない | ポート。`$PORT` を上書きしない（Dockerfile はそのままでよい） |
| ビルドが遅い / Buildpacks が走る | ビルドタイプが Dockerfile になっているか確認する |
| 会議データが再起動で消える | `/healthz` の `repository` が `memory` に落ちている。Firestore の DB 作成か IAM 権限を確認する |

## 費用について

Cloud Run はリクエストが無ければ課金されない（最小インスタンス0が既定）。
デモ後に止めたい場合は削除するか、`--max-instances` を絞っておく。
Firestore と Artifact Registry は保管量に応じた課金が残るので、
不要になったらデータベースとリポジトリも消す。
