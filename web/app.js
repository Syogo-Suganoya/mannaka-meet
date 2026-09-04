/* マンナカ アプリ本体。
   ログインは無い。参加者は毎回フォームから受け取り、結果はその場で見比べる。 */

const API = "/api";
const $ = (id) => document.getElementById(id);

const state = {
  rows: [], // 入力中の参加者 [{name, station}]
  meeting: null,
  policy: "minimax",
  focused: null, // 表情を出している候補地の駅名
  map: null, // MapLibre のインスタンス。候補を切り替えても作り直さない
  mapReady: false, // スタイルの読み込みが済んだか
  mapDead: false, // 地図を諦めて略図に切り替えたか
  mapFitted: false, // 一度でも範囲を合わせたか（初回だけ動かさずに飛ばす）
  pins: [], // 駅のマーカー。描き直すたびに入れ替える
};

/* 評価の基準。専門語を先に出さず、まず一言で言える形にする。
   アイコンは路線図と同じ丸と線分の語彙で描く。 */
const POLICIES = [
  {
    key: "sum",
    label: "早い",
    note: "みんなの移動時間の合計が最小",
    icon: `<circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" stroke-width="2.6"/>
           <path d="M12 7 L12 12 L16 14" fill="none" stroke="currentColor"
                 stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>`,
  },
  {
    key: "minimax",
    label: "バランスよく",
    note: "いちばん遠い人の負担を軽く",
    icon: `<path d="M4 8 L20 8" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/>
           <path d="M12 8 L12 19" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/>
           <path d="M7 19 L17 19" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/>
           <circle cx="4" cy="8" r="2.4" fill="currentColor"/>
           <circle cx="20" cy="8" r="2.4" fill="currentColor"/>`,
  },
  {
    key: "cost",
    label: "安い",
    note: "みんなの運賃の合計が最小",
    icon: `<circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" stroke-width="2.6"/>
           <path d="M9 8.5 L12 12 L15 8.5 M8.5 13 L15.5 13 M8.5 15.6 L15.5 15.6 M12 12 L12 16.5"
                 fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>`,
  },
];

// ---------------------------------------------------------------- 通信

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function api(path, options = {}) {
  const res = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (res.status === 204) return null;
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, body.detail || res.statusText);
  return body;
}

function toast(text, bad = false) {
  const el = $("toast");
  el.textContent = text;
  el.classList.toggle("bad", bad);
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (el.hidden = true), 4200);
}

/** 想定内のエラーは文言を出し、想定外は投げ直して気づけるようにする */
async function guard(fn) {
  try {
    return await fn();
  } catch (e) {
    if (e instanceof ApiError) {
      toast(e.message, true);
      return null;
    }
    throw e;
  }
}

const when = (iso) =>
  new Date(iso).toLocaleString("ja-JP", {
    month: "2-digit", day: "2-digit", weekday: "short",
    hour: "2-digit", minute: "2-digit",
  });
const yen = (n) => `${n.toLocaleString()}円`;
const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );

// ---------------------------------------------------------------- 起動

function boot() {
  $("rq-when").value = defaultWhen();
  if (state.rows.length === 0) {
    state.rows = [row(), row(), row()];
  }
  renderRows();
  openRequest();
  window.addEventListener("resize", () => {
    measureListHead();
    // 幅が変わると地図が畳まれたり広がったりする。合わせ直さないと枠外に出る
    if (state.meeting) renderMap();
  });
  // 3カラムに戻ったら地図も様子も並んで見えている。面は用済み
  window.matchMedia(NARROW).addEventListener("change", (e) => {
    if (!e.matches) closeSheet();
  });
}

/** datetime-local は現地時刻の文字列。翌日14時を既定にする。 */
function defaultWhen() {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  d.setHours(14, 0, 0, 0);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(
    d.getHours()
  )}:${pad(d.getMinutes())}`;
}

// ---------------------------------------------------------------- モーダル

const hasMeeting = () => document.body.classList.contains("has-meeting");

function openRequest() {
  closeSheet();
  $("request-modal").hidden = false;
  $("request-close").hidden = !hasMeeting();
}

function closeRequest() {
  if (!hasMeeting()) return; // まだ後ろに見せるものが無い
  $("request-modal").hidden = true;
}

$("edit-request").onclick = openRequest;
$("request-close").onclick = closeRequest;
$("request-modal").onclick = (e) => {
  if (e.target === $("request-modal")) closeRequest();
};
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!$("request-modal").hidden) closeRequest();
  else closeSheet();
});

/* 狭い画面では地図と様子を畳んでいる。候補を押すと、その候補と地図と様子を
   ひとつの面にまとめて開く。要素は元の位置に置いたまま CSS で持ち上げるので、
   地図は作り直さない。 */
/* 3カラムが崩れる幅では、地図も様子も畳まれている。CSS 側の境目と合わせる。 */
const NARROW = "(max-width: 1080px)";
const isNarrow = () => window.matchMedia(NARROW).matches;

function openSheet() {
  document.body.dataset.sheet = "detail";
  // 畳んでいる間は幅が0で、その寸法のまま地図を作ると範囲を合わせられない
  requestAnimationFrame(renderMap);
}

function closeSheet() {
  delete document.body.dataset.sheet;
}

$("sheet-back").onclick = closeSheet;
$("sheet-close").onclick = closeSheet;

// ---------------------------------------------------------------- 入力行

const row = (name = "", station = "") => ({ name, station });

function capture() {
  const names = [...document.querySelectorAll(".member-name")];
  const stations = [...document.querySelectorAll(".member-station")];
  state.rows = names.map((n, i) => row(n.value, stations[i].value));
}

function renderRows() {
  $("rq-members").innerHTML = state.rows
    .map(
      (r, i) => `<div class="member">
        <input class="member-name" type="text" value="${esc(r.name)}"
          placeholder="名前" aria-label="${i + 1}人目の名前">
        <input class="member-station" type="text" value="${esc(r.station)}"
          placeholder="出発駅" aria-label="${i + 1}人目の出発駅">
        <button type="button" class="member-remove" data-i="${i}"
          aria-label="${i + 1}人目を削除" ${state.rows.length <= 1 ? "disabled" : ""}>×</button>
      </div>`
    )
    .join("");

  $("rq-members").querySelectorAll(".member-remove").forEach((b) => {
    b.onclick = () => {
      capture();
      state.rows.splice(Number(b.dataset.i), 1);
      renderRows();
    };
  });
  $("rq-count").textContent = `${state.rows.length} 名`;
}

$("rq-add").onclick = () => {
  capture();
  state.rows.push(row());
  renderRows();
  $("rq-members").querySelector(".member:last-child .member-name").focus();
};

$("request-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  capture();
  const participants = state.rows
    .filter((r) => r.station.trim())
    .map((r) => ({ name: r.name.trim(), origin_station: r.station.trim() }));
  if (participants.length < 2) {
    return toast("出発駅を2人以上入れてください", true);
  }

  const btn = $("request-form").querySelector("button[type=submit]");
  btn.disabled = true;
  await guard(async () => {
    const meeting = await api("/meetings", {
      method: "POST",
      body: JSON.stringify({
        starts_at: $("rq-when").value,
        participants,
        notes: $("rq-notes").value.trim(),
      }),
    });
    state.meeting = meeting;
    state.policy = meeting.policy || "minimax";
    state.focused = null;
    renderMeeting();
  });
  btn.disabled = false;
});

// ---------------------------------------------------------------- 会議

function renderMeeting() {
  const m = state.meeting;
  document.body.classList.add("has-meeting");
  $("edit-request").hidden = false;
  closeRequest();

  $("meeting").hidden = false;
  $("m-title").textContent = `${when(m.starts_at)} / ${m.participant_count}名`;

  renderPolicyTabs();
  renderCandidates();
  renderMap();
  renderFaces();
  measureListHead();
}

/* 選択中の候補は貼り付いた見出しの下で止まる。見出しの高さは字の折り返しで
   変わるので、決め打ちにせず測って渡す。 */
function measureListHead() {
  const head = document.querySelector(".list-head");
  if (!head) return;
  const h = Math.round(head.getBoundingClientRect().height);
  document.documentElement.style.setProperty("--list-head-h", `${h}px`);
}

function renderPolicyTabs() {
  $("policy-tabs").innerHTML = POLICIES.map(
    (p) => `<button class="ptab" role="tab" data-policy="${p.key}"
      aria-selected="${p.key === state.policy}">
      <svg viewBox="0 0 24 24" aria-hidden="true">${p.icon}</svg>
      <b>${p.label}</b><small>${p.note}</small></button>`
  ).join("");

  document.querySelectorAll(".ptab").forEach((tab) => {
    tab.onclick = async () => {
      state.policy = tab.dataset.policy;
      state.focused = null;
      await guard(async () => {
        state.meeting = await api(`/meetings/${state.meeting.meeting_id}/optimize`, {
          method: "POST",
          body: JSON.stringify({ policy: state.policy }),
        });
        renderMeeting();
      });
    };
  });
}

/** 表情を出している候補。既定はその基準での最良。 */
function focusedCandidate() {
  const list = state.meeting.optimization.candidates;
  return list.find((c) => c.station === state.focused) || list[0];
}

function renderCandidates() {
  const opt = state.meeting.optimization;
  if (!opt) return ($("candidates").innerHTML = "");
  const focused = focusedCandidate();

  const worst = Math.max(
    ...opt.candidates.flatMap((c) => c.breakdown.map((b) => b.duration_minutes)),
    1
  );
  $("candidates").innerHTML = opt.candidates
    .map(
      (c) => `<div class="cand${c.rank === 1 ? " top" : ""}${
        c.station === focused.station ? " focused" : ""
      }" data-station="${esc(c.station)}" tabindex="0" role="button"
        aria-pressed="${c.station === focused.station}">
        <div class="cand-head">
          <span class="cand-name">${esc(c.area)}（${esc(c.station)}）</span>
          ${c.rank === 1 ? '<span class="cand-pick">おすすめ</span>' : ""}
        </div>
        <div class="metrics">
          <span>いちばん遠い人 <b>${c.max_minutes}</b>分</span>
          <span>平均 <b>${c.average_minutes}</b>分</span>
          <span>差 <b>${c.unfairness_minutes}</b>分</span>
        </div>
        <div class="bars">
          ${c.breakdown
            .map(
              (b) => `<div class="bar">
                <span class="who">${esc(b.participant)}<small>${esc(b.origin_station)}</small></span>
                <span class="track"><span class="fill" style="width:${Math.max(
                  4, (b.duration_minutes / worst) * 100
                )}%"></span></span>
                <span>${b.duration_minutes}分 / ${yen(b.fare_yen)} / 乗換${b.transfers}回</span>
              </div>`
            )
            .join("")}
        </div>
      </div>`
    )
    .join("");

  document.querySelectorAll(".cand").forEach((el) => {
    const pick = () => {
      state.focused = el.dataset.station;
      renderCandidates();
      renderMap();
      renderFaces();
      // 3カラムなら地図も様子も見えている。狭い画面だけ面を開く
      if (isNarrow()) openSheet();
    };
    el.onclick = pick;
    el.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        pick();
      }
    };
  });
}

// ---------------------------------------------------------------- 地図

/* 選択中の候補と、そこへ向かう全員の出発駅。座標は [緯度, 経度]。
   結ぶのは直線で、実際の路線ではない。見せたいのは経路ではなく距離の偏り。 */

// 地形も国境も要らないので、灰色の下地だけの Positron を使う。鍵は不要。
const MAP_STYLE = "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json";
const lngLat = ([lat, lng]) => [lng, lat];
const cssVar = (name) =>
  getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim();

function renderMap() {
  const c = focusedCandidate();
  const places = state.meeting.optimization.places || {};
  const rows = c.breakdown.filter((b) => places[b.origin_station]);
  const hub = places[c.station];

  if (!hub || rows.length === 0) {
    disposeMap();
    $("map").innerHTML = `<p class="hint">この経路プロバイダは位置を出せないため、地図は表示できません。</p>`;
    return;
  }

  // タイルを読めない環境（CDN遮断・オフライン）でも地図の役目は果たしたいので、
  // その場合は座標だけで自前の略図を描く。
  if (window.maplibregl && !state.mapDead) drawLiveMap(c, hub, rows, places);
  else drawFlatMap(c, hub, rows, places);
}

/** 地図を諦めて略図に切り替える。一度諦めたらこの画面では再挑戦しない。 */
function giveUpMap() {
  if (state.mapDead) return;
  state.mapDead = true;
  disposeMap();
  $("map").innerHTML = "";
  renderMap();
}

function disposeMap() {
  if (state.map) state.map.remove();
  state.map = null;
  state.mapReady = false;
  state.mapFitted = false;
  state.pins = [];
}

function drawLiveMap(c, hub, rows, places) {
  const box = $("map");
  // 狭い画面では畳まれていることがある。寸法が無いまま作っても範囲を合わせられ
  // ないので、開かれたときに openSheet からもう一度呼んでもらう
  if (box.clientWidth === 0) return;

  if (!state.map) {
    box.innerHTML = "";
    state.map = new maplibregl.Map({
      container: box,
      style: MAP_STYLE,
      // 初期位置を指定しないと、スタイルを読み終えた時点で style.json の既定
      // （経緯度0・ズーム0）に引き戻され、直前に合わせた範囲が消えることがある
      center: lngLat(hub),
      zoom: 9,
      // 見るための地図なので、回転と傾きは殺しておく
      dragRotate: false,
      pitchWithRotate: false,
      touchPitch: false,
      attributionControl: { compact: true },
    });
    // タイルが返ってこないと load は永遠に来ないので、待つのは style.load まで。
    // それも来ないなら地図は諦める（真っ白な枠を残すより略図のほうがましなので）。
    setTimeout(() => {
      if (!state.mapReady) giveUpMap();
    }, 6000);
    state.map.on("style.load", () => {
      state.map.addSource("routes", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      state.map.addLayer({
        id: "routes",
        type: "line",
        source: "routes",
        layout: { "line-cap": "round" },
        paint: {
          "line-color": ["get", "color"],
          "line-width": 4,
          "line-opacity": 0.85,
        },
      });
      state.mapReady = true;
      renderMap(); // タイルが載ってから中身を入れ直す
    });
    return;
  }
  if (!state.mapReady) return;

  state.map.getSource("routes").setData({
    type: "FeatureCollection",
    features: rows.map((b) => ({
      type: "Feature",
      properties: { color: cssVar(moodOf(b, c).color) },
      geometry: {
        type: "LineString",
        coordinates: [lngLat(places[b.origin_station]), lngLat(hub)],
      },
    })),
  });

  state.pins.forEach((m) => m.remove());
  state.pins = rows.map((b) =>
    new maplibregl.Marker({
      element: pin(b.origin_station, cssVar(moodOf(b, c).color), b.participant),
    })
      .setLngLat(lngLat(places[b.origin_station]))
      .addTo(state.map)
  );
  state.pins.push(
    new maplibregl.Marker({ element: pin(c.station) })
      .setLngLat(lngLat(hub))
      .addTo(state.map)
  );

  const bounds = new maplibregl.LngLatBounds();
  [hub, ...rows.map((b) => places[b.origin_station])].forEach((p) =>
    bounds.extend(lngLat(p))
  );
  // 枠の寸法が変わっていたら測り直す（毎回呼ぶとタイルの読み込みを取りこぼす）
  if (Math.abs(state.map.transform.width - box.clientWidth) > 2) state.map.resize();
  // 駅名は丸の下に出るので、左右と下は名前のぶんだけ余分に空ける。
  // 初回だけは動かさずに飛ばす。開いた直後は枠がまだ動いていて、
  // 動きの途中で寸法が変わると中断され、初期位置のまま取り残される。
  state.map.fitBounds(bounds, {
    padding: { top: 38, bottom: 46, left: 64, right: 64 },
    maxZoom: 12.5,
    duration: state.mapFitted ? 500 : 0,
  });
  state.mapFitted = true;
}

/** 出発駅は色付きの白丸、候補地は塗りつぶした丸。ページの署名と同じ書き分け。
    出発駅には誰の駅かを添える。名前が先で駅は添え物（見たいのは人の並び）。 */
function pin(station, color, person) {
  const el = document.createElement("div");
  el.className = color ? "pin" : "pin pin-hub";
  const label = person
    ? `<b>${esc(person)}</b><small>${esc(station)}</small>`
    : esc(station);
  el.innerHTML =
    `<span class="pin-dot"${color ? ` style="border-color:${color}"` : ""}></span>` +
    `<span class="pin-name">${label}</span>`;
  return el;
}

// -- タイルが無いときの略図 ----------------------------------------------

const MAP_W = 320;
const MAP_H = 300;
const MAP_PAD = 34;

function drawFlatMap(c, hub, rows, places) {
  // 経度1度は緯度1度より短い。緯度で縮めないと東西に伸びた地図になる
  const shrink = Math.cos((hub[0] * Math.PI) / 180);
  const flat = ([lat, lng]) => [lng * shrink, lat];
  const pts = [hub, ...rows.map((b) => places[b.origin_station])].map(flat);
  const xs = pts.map((p) => p[0]);
  const ys = pts.map((p) => p[1]);
  const spanX = Math.max(...xs) - Math.min(...xs) || 1;
  const spanY = Math.max(...ys) - Math.min(...ys) || 1;
  // 縦横の比率を保つ。潰すと距離感が嘘になる
  const scale = Math.min((MAP_W - MAP_PAD * 2) / spanX, (MAP_H - MAP_PAD * 2) / spanY);
  const midX = (Math.max(...xs) + Math.min(...xs)) / 2;
  const midY = (Math.max(...ys) + Math.min(...ys)) / 2;
  // SVG は下が正なので南北は反転する
  const at = ([x, y]) => [
    MAP_W / 2 + (x - midX) * scale,
    MAP_H / 2 - (y - midY) * scale,
  ];

  const [hx, hy] = at(flat(hub));
  const lines = rows
    .map((b) => {
      const [x, y] = at(flat(places[b.origin_station]));
      const mood = moodOf(b, c);
      return `<path d="M${x} ${y} L${hx} ${hy}" fill="none" stroke="var(--${mood.color})"
        stroke-width="4" stroke-linecap="round" opacity=".85"/>`;
    })
    .join("");

  const dots = rows
    .map((b) => {
      const [x, y] = at(flat(places[b.origin_station]));
      const mood = moodOf(b, c);
      // ラベルは点の上か下。横に出すと近い駅どうしで重なる
      const below = y > hy;
      const top = below ? y + 20 : y - 24;
      return `<circle cx="${x}" cy="${y}" r="6" fill="#fff"
          stroke="var(--${mood.color})" stroke-width="3.5"/>
        <text x="${x}" y="${top}" text-anchor="middle"
          font-size="11" font-weight="700" fill="var(--ink)">${esc(b.participant)}</text>
        <text x="${x}" y="${top + 12}" text-anchor="middle"
          font-size="10" fill="var(--sub)">${esc(b.origin_station)}</text>`;
    })
    .join("");

  $("map").innerHTML = `<svg viewBox="0 0 ${MAP_W} ${MAP_H}" role="img"
    aria-label="${esc(c.station)}に集まる場合の、各自の出発駅と距離">
    ${lines}${dots}
    <circle cx="${hx}" cy="${hy}" r="19" fill="var(--ink)"/>
    <text x="${hx}" y="${hy + 4}" text-anchor="middle" font-size="12"
      fill="#fff" font-weight="700">${esc(c.station)}</text>
  </svg>`;
}

// ---------------------------------------------------------------- 表情

/* 負担の重さを 0（いちばん楽）〜1（いちばん重い）に正規化して表情を選ぶ。
   順位ではなく相対値にしているのは、全員が同程度のときに
   最下位の1人だけ不機嫌にしないため。 */
const MOODS = [
  { upTo: 0.20, key: "great", color: "green", label: "楽" },
  { upTo: 0.45, key: "good", color: "green", label: "やや楽" },
  { upTo: 0.68, key: "ok", color: "blue", label: "ふつう" },
  { upTo: 0.88, key: "heavy", color: "yellow", label: "重い" },
  { upTo: 1.01, key: "worst", color: "red", label: "いちばん重い" },
];

const moodFor = (ratio) => MOODS.find((m) => ratio <= m.upTo) || MOODS[MOODS.length - 1];

/** 路線図と同じ language で描く。頭は駅の丸、手足は路線の線分。

    人に見せるには長さの差が要る: 脚 > 胴 > 腕。
    腕と脚は頭より先に描いて後ろに回す。 */
const X = 36; // 体の中心

function personSvg(mood) {
  const c = `var(--${mood.color})`;
  const armsUp = mood.key === "great";
  const droop = mood.key === "heavy" || mood.key === "worst";

  const headY = droop ? 25 : 21; // うなだれると頭が下がる
  const neck = headY + 17;
  const sh = headY + 25; // 肩
  const hip = headY + 51; // 腰（胴 = 34）
  const foot = hip + 33; // 足先（脚 = 33）

  const arms = armsUp
    ? `<path d="M${X} ${sh} L14 ${headY + 1}" ${stroke(c)}/>
       <path d="M${X} ${sh} L58 ${headY + 1}" ${stroke(c)}/>`
    : droop
    ? // 力なく垂れる。閉じた脚と重ならないよう、手先は脚より外に置く
      `<path d="M${X} ${sh} L23 ${sh + 26}" ${stroke(c)}/>
       <path d="M${X} ${sh} L49 ${sh + 26}" ${stroke(c)}/>`
    : `<path d="M${X} ${sh} L18 ${sh + 22}" ${stroke(c)}/>
       <path d="M${X} ${sh} L54 ${sh + 22}" ${stroke(c)}/>`;

  // 楽なときは足を開き、重いときは閉じる
  const spread = armsUp ? 19 : droop ? 8 : 14;
  const legs = `<path d="M${X} ${hip} L${X - spread} ${foot}" ${stroke(c)}/>
    <path d="M${X} ${hip} L${X + spread} ${foot}" ${stroke(c)}/>`;

  const mouth = {
    great: `M29 ${headY + 4} Q${X} ${headY + 13} 43 ${headY + 4}`,
    good: `M30 ${headY + 5} Q${X} ${headY + 12} 42 ${headY + 5}`,
    ok: `M30 ${headY + 8} L42 ${headY + 8}`,
    heavy: `M30 ${headY + 11} Q${X} ${headY + 5} 42 ${headY + 11}`,
    worst: `M29 ${headY + 12} Q${X} ${headY + 3} 43 ${headY + 12}`,
  }[mood.key];

  const eyes =
    mood.key === "great"
      ? `<path d="M28 ${headY - 3} Q31 ${headY - 8} 34 ${headY - 3}" fill="none" stroke="var(--ink)" stroke-width="2.2" stroke-linecap="round"/>
         <path d="M38 ${headY - 3} Q41 ${headY - 8} 44 ${headY - 3}" fill="none" stroke="var(--ink)" stroke-width="2.2" stroke-linecap="round"/>`
      : `<circle cx="31" cy="${headY - 4}" r="2" fill="var(--ink)"/>
         <circle cx="41" cy="${headY - 4}" r="2" fill="var(--ink)"/>`;

  const sweat =
    mood.key === "worst"
      ? `<path d="M56 ${headY - 12} q3.5 6 0 8.5 q-3.5 -2.5 0 -8.5z" fill="var(--blue)" opacity=".8"/>`
      : "";

  return `<svg class="person" viewBox="0 0 72 120" role="img" aria-label="${mood.label}">
    ${arms}
    ${legs}
    <path d="M${X} ${neck} L${X} ${hip}" ${stroke(c)}/>
    <circle cx="${X}" cy="${headY}" r="15" fill="#fff" stroke="${c}" stroke-width="5"/>
    ${eyes}
    <path d="${mouth}" fill="none" stroke="var(--ink)" stroke-width="2.5" stroke-linecap="round"/>
    ${sweat}
  </svg>`;
}

const stroke = (c) =>
  `fill="none" stroke="${c}" stroke-width="7" stroke-linecap="round"`;

/** その候補地における、この人の機嫌。地図の線の色にも同じものを使う。 */
function moodOf(row, candidate) {
  const times = candidate.breakdown.map((b) => b.duration_minutes);
  const lo = Math.min(...times);
  const span = Math.max(...times) - lo;
  // 全員が同程度なら誰も不機嫌にしない
  return moodFor(span === 0 ? 0.3 : (row.duration_minutes - lo) / span);
}

function renderFaces() {
  const c = focusedCandidate();

  // 入力順に並べる。候補を切り替えても同じ人が同じ位置に居るようにするため。
  const rows = [...c.breakdown].sort(
    (a, b) => Number(a.uid.slice(1)) - Number(b.uid.slice(1))
  );

  $("faces").innerHTML = rows
    .map((b) => {
      const mood = moodOf(b, c);
      return `<figure class="face m-${mood.color}"
        title="${esc(b.participant)}: ${b.duration_minutes}分 / ${yen(b.fare_yen)}">
        ${personSvg(mood)}
        <figcaption>${esc(b.participant)}</figcaption>
      </figure>`;
    })
    .join("");
}

boot();
