const API = "/api";
const state = { meeting: null, policy: "minimax", station: null, users: [], organizerUid: null };

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const res = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `${res.status} ${res.statusText}`);
  return body;
}

function notice(el, text, kind = "") {
  el.innerHTML = `<div class="notice ${kind}">${text}</div>`;
}

// ------------------------------------------------------------ 起動

async function loadProviders() {
  const health = await fetch("/healthz").then((r) => r.json());
  $("providers").innerHTML = Object.entries(health.providers)
    .map(([k, v]) => `<span class="${v === "mock" || v === "stub" || v === "memory" ? "mock" : ""}">${k}: ${v}</span>`)
    .join("");
}

async function loadUsers() {
  state.users = await api("/users");
  $("participants").innerHTML = state.users.length
    ? state.users
        .map((u) => `<span class="chip">${u.display_name}（${u.origin_station}）</span>`)
        .join("")
    : '<span class="empty">「デモ参加者を登録」から始めてください</span>';

  for (const id of ["author-select", "inbox-select"]) {
    const select = $(id);
    const current = select.value;
    select.innerHTML = state.users
      .map((u) => `<option value="${u.uid}">${u.display_name}</option>`)
      .join("");
    if (current) select.value = current;
  }
}

// ------------------------------------------------------------ 1. チャットで依頼

$("seed-btn").onclick = async () => {
  await api("/demo/seed", { method: "POST" });
  await loadUsers();
  await Promise.all([loadChat(), loadInbox()]);
};

$("create-btn").onclick = async () => {
  await loadUsers();
  if (!state.users.length)
    return notice($("meeting-summary"), "先に参加者を登録してください", "error");

  const text = $("request-text").value.trim();
  if (!text) return;
  const res = await api("/chat/messages", {
    method: "POST",
    body: JSON.stringify({ text, author_uid: $("author-select").value }),
  });
  $("request-text").value = "";
  await loadChat();

  if (!res.meeting) {
    // メンションが無い発言は記録するだけ
    return notice($("meeting-summary"), "「@マンナカ」を付けると依頼として解釈します。");
  }
  state.meeting = res.meeting;
  state.organizerUid = $("author-select").value;
  renderSummary();
  selectPolicy(state.meeting.policy, false);
  const diff = await api(`/meetings/${state.meeting.meeting_id}/policy-comparison`);
  renderCandidates(diff);
  await Promise.all([loadInbox(), loadAudit()]);
};

async function loadChat() {
  const messages = await api("/chat/messages");
  $("chat").innerHTML = messages.length
    ? messages
        .map(
          (m) => `<div class="msg ${m.is_agent ? "agent" : ""}">
            <span class="to">${m.author_name}${m.is_agent ? "（エージェント）" : ""} ・ ${new Date(
            m.created_at
          ).toLocaleTimeString("ja-JP", { hour: "2-digit", minute: "2-digit" })}</span>${m.text}</div>`
        )
        .join("")
    : '<span class="empty">まだ発言はありません</span>';
  $("chat").scrollTop = $("chat").scrollHeight;
}

function renderSummary() {
  const m = state.meeting;
  const organizer = organizerUid();
  $("participants").innerHTML = state.users
    .map(
      (u) =>
        `<span class="chip ${u.uid === organizer ? "organizer" : ""}">${u.display_name}（${u.origin_station}）${u.uid === organizer ? " 主催" : ""}</span>`
    )
    .join("");
  $("meeting-summary").innerHTML =
    `<b>${m.meeting_id}</b> ／ ${new Date(m.starts_at).toLocaleString("ja-JP", {
      month: "2-digit", day: "2-digit", weekday: "short", hour: "2-digit", minute: "2-digit",
    })} 開始 ／ ${m.duration_minutes}分 ／ ${m.participant_count}名` +
    (m.equipment.length ? ` ／ 設備: ${m.equipment.join("・")}` : "");
}

// ------------------------------------------------------------ 2. 候補

document.querySelectorAll("#policy-tabs .policy").forEach((btn) => {
  btn.onclick = () => selectPolicy(btn.dataset.policy, true);
});

function selectPolicy(policy, rerun) {
  state.policy = policy;
  document.querySelectorAll("#policy-tabs .policy").forEach((b) => {
    b.classList.toggle("active", b.dataset.policy === policy);
  });
  if (rerun && state.meeting) optimize();
}

async function optimize() {
  if (!state.meeting) return;
  state.meeting = await api(`/meetings/${state.meeting.meeting_id}/optimize`, {
    method: "POST",
    body: JSON.stringify({ policy: state.policy }),
  });
  const diff = await api(`/meetings/${state.meeting.meeting_id}/policy-comparison`);
  renderCandidates(diff);
  $("rooms").innerHTML = "";
  $("approval").innerHTML = "";
  await Promise.all([loadInbox(), loadAudit()]);
}

function renderCandidates(diff) {
  const opt = state.meeting.optimization;
  $("explanation").textContent = opt.explanation || "";

  $("policy-diff").innerHTML = Object.entries(diff)
    .map(
      ([key, v]) =>
        `<div><strong>${v.label}（${key}）</strong>${v.station}／合計${v.total_minutes}分・最長${v.max_minutes}分・${v.total_fare_yen.toLocaleString()}円</div>`
    )
    .join("");

  const worst = Math.max(...opt.candidates.flatMap((c) => c.breakdown.map((b) => b.duration_minutes)), 1);
  $("candidates").innerHTML = opt.candidates
    .map(
      (c) => `
      <div class="candidate ${c.rank === 1 ? "best" : ""}">
        <header>
          <span class="name">${c.area}（${c.station}）</span>
          <span class="rank">${c.rank === 1 ? "★ 最良" : `${c.rank}位`}</span>
        </header>
        <div class="metrics">
          <span>合計 <b>${c.total_minutes}</b>分</span>
          <span>最長 <b>${c.max_minutes}</b>分</span>
          <span>平均 <b>${c.average_minutes}</b>分</span>
          <span>総額 <b>${c.total_fare_yen.toLocaleString()}</b>円</span>
          <span>偏り <b>${c.unfairness_minutes}</b>分</span>
        </div>
        <div class="bars">
          ${c.breakdown
            .map(
              (b) => `<div class="bar">
                <span>${b.participant}</span>
                <span><span class="fill" style="width:${Math.max(4, (b.duration_minutes / worst) * 100)}%"></span></span>
                <span>${b.duration_minutes}分 / ${b.fare_yen.toLocaleString()}円 / 乗換${b.transfers}回</span>
              </div>`
            )
            .join("")}
        </div>
        <div class="row">
          <button data-station="${c.station}" class="pick">この場所で会議室を探す</button>
        </div>
      </div>`
    )
    .join("");

  document.querySelectorAll(".pick").forEach((btn) => {
    btn.onclick = () => loadRooms(btn.dataset.station);
  });
}

// ------------------------------------------------------------ 3. 手配

async function loadRooms(station) {
  state.station = station;
  const { rooms } = await api(`/meetings/${state.meeting.meeting_id}/rooms?station=${encodeURIComponent(station)}`);
  if (!rooms.length) {
    $("rooms").innerHTML = `<p class="empty">${station} に条件を満たす空き会議室がありません</p>`;
    return;
  }
  $("rooms").innerHTML = `
    <table>
      <tr><th>${station} 周辺の会議室</th><th>定員</th><th>徒歩</th><th>設備</th><th class="num">料金</th><th></th></tr>
      ${rooms
        .map(
          (r) => `<tr>
            <td>${r.name}</td>
            <td class="num">${r.capacity}名</td>
            <td class="num">${r.walk_minutes}分</td>
            <td>${r.equipment.join("・")}</td>
            <td class="num">${r.price_yen ? r.price_yen.toLocaleString() + "円" : "無料"}</td>
            <td><button class="ghost hold" data-room="${r.room_id}">仮押さえ</button></td>
          </tr>`
        )
        .join("")}
    </table>`;
  document.querySelectorAll(".hold").forEach((btn) => {
    btn.onclick = () => stage(btn.dataset.room);
  });
  await loadAudit();
}

async function stage(roomId) {
  state.meeting = await api(`/meetings/${state.meeting.meeting_id}/arrange`, {
    method: "POST",
    body: JSON.stringify({ station: state.station, room_id: roomId }),
  });
  renderApproval();
  await loadAudit();
}

function renderApproval() {
  const m = state.meeting;
  const a = m.approval;
  if (!a) return ($("approval").innerHTML = "");

  if (a.status === "auto_approved") {
    $("approval").innerHTML =
      `<div class="notice ok">無料の会議室のため支出承認は不要です。</div>
       <div class="row"><button id="confirm-btn">確定して全員に配信</button></div>`;
  } else if (a.status === "pending") {
    const over = a.amount_yen > a.spend_limit_yen;
    $("approval").innerHTML = `
      <div class="notice ${over ? "error" : ""}">
        <b>主催者の承認が必要です</b><br>
        ${a.reason}<br>
        金額 ${a.amount_yen.toLocaleString()}円 ／ エージェントの支出上限 ${a.spend_limit_yen.toLocaleString()}円
        ${over ? "<br>上限を超えているため、このままでは確定できません。" : ""}
      </div>
      <div class="row">
        <button id="approve-btn" ${over ? "disabled" : ""}>承認する（主催者）</button>
        <button id="reject-btn" class="ghost">却下</button>
      </div>`;
    $("approve-btn").onclick = () => decideApproval(true);
    $("reject-btn").onclick = () => decideApproval(false);
    return;
  } else if (a.status === "approved") {
    $("approval").innerHTML =
      `<div class="notice ok">承認済み（${a.approver_uid} / ${a.amount_yen.toLocaleString()}円）</div>
       <div class="row"><button id="confirm-btn">確定して全員に配信</button></div>`;
  } else {
    $("approval").innerHTML = `<div class="notice error">承認が却下されました。仮押さえは解放済みです。</div>`;
    return;
  }
  $("confirm-btn").onclick = confirm_;
}

async function decideApproval(approved) {
  try {
    state.meeting = await api(`/meetings/${state.meeting.meeting_id}/approval`, {
      method: "POST",
      body: JSON.stringify({ approved, actor_uid: organizerUid() }),
    });
    renderApproval();
  } catch (e) {
    notice($("approval"), e.message, "error");
  }
  await loadAudit();
}

async function confirm_() {
  state.meeting = await api(`/meetings/${state.meeting.meeting_id}/confirm`, {
    method: "POST",
    body: JSON.stringify({ station: state.station }),
  });
  const d = state.meeting.decision;
  $("approval").innerHTML = `<div class="notice ok">
      確定: ${d.area}（${d.station}）${d.room ? " / " + d.room.name : ""}<br>
      採用した公平性基準: ${state.meeting.optimization.policy_label}（${d.policy}）<br>
      カレンダー登録: ${d.calendar_event_id} ／ ${state.meeting.dispatches.length}名へ個別配信済み
    </div>`;
  await Promise.all([loadInbox(), loadAudit()]);
}

// ------------------------------------------------------------ 4. 当日フォロー

$("disrupt-btn").onclick = async () => {
  await api("/demo/disruption", {
    method: "POST",
    body: JSON.stringify({ line: "JR中央線", delay_minutes: 18 }),
  });
  notice($("followup"), "JR中央線に18分の遅延を発生させました。「運行状況を確認」を押してください。");
};

$("followup-btn").onclick = async () => {
  if (!state.meeting || !state.meeting.decision)
    return notice($("followup"), "先に会議を確定してください", "error");
  try {
    const res = await api(`/meetings/${state.meeting.meeting_id}/follow-up`, { method: "POST" });
    if (!res.proposal) {
      notice($("followup"), "影響のある遅延はありません。予定どおり開始します。", "ok");
    } else {
      const p = res.proposal;
      $("followup").innerHTML = `
        <div class="notice">
          <b>開始時刻の調整を起案しました（確定ではありません）</b><br>
          ${p.rationale}<br>
          影響 ${p.affected_uids.length}名 ／ ${new Date(p.current_start).toLocaleTimeString("ja-JP", { hour: "2-digit", minute: "2-digit" })} → ${new Date(p.proposed_start).toLocaleTimeString("ja-JP", { hour: "2-digit", minute: "2-digit" })}
        </div>
        <div class="row">
          <button id="accept-btn">承諾して時刻を変更</button>
          <button id="decline-btn" class="ghost">見送る</button>
        </div>`;
      $("accept-btn").onclick = () => decideProposal(p.proposal_id, true);
      $("decline-btn").onclick = () => decideProposal(p.proposal_id, false);
    }
  } catch (e) {
    notice($("followup"), e.message, "error");
  }
  await Promise.all([loadInbox(), loadAudit()]);
};

async function decideProposal(proposalId, accepted) {
  state.meeting = await api(`/meetings/${state.meeting.meeting_id}/proposal`, {
    method: "POST",
    body: JSON.stringify({ proposal_id: proposalId, accepted, actor_uid: organizerUid() }),
  });
  notice(
    $("followup"),
    accepted
      ? `開始を ${new Date(state.meeting.starts_at).toLocaleTimeString("ja-JP", { hour: "2-digit", minute: "2-digit" })} に変更し、全員へ再配信しました。`
      : "起案は見送られました。予定どおり開始します。",
    "ok"
  );
  renderSummary();
  await Promise.all([loadInbox(), loadAudit()]);
}

// ------------------------------------------------------------ 5. 可観測性

function organizerUid() {
  return state.organizerUid || (state.users[0] && state.users[0].uid);
}

async function loadInbox() {
  const uid = $("inbox-select").value;
  if (!uid) return;
  const items = await api(`/notifications?uid=${encodeURIComponent(uid)}`);
  $("inbox").innerHTML = items.length
    ? items
        .map(
          (n) => `<div class="msg ${n.read ? "" : "unread"}" data-id="${n.notification_id}">
            <span class="to">${n.title}${n.read ? "" : " ・ 未読（クリックで既読）"}</span>${n.body}</div>`
        )
        .join("")
    : '<span class="empty">この人宛の通知はまだありません</span>';

  document.querySelectorAll("#inbox .msg.unread").forEach((el) => {
    el.onclick = async () => {
      await api(`/notifications/${el.dataset.id}/read`, { method: "POST" });
      await loadInbox();
    };
  });
}

async function loadAudit() {
  if (!state.meeting) return;
  const logs = await api(`/meetings/${state.meeting.meeting_id}/audit`);
  $("audit").innerHTML = logs.length
    ? logs
        .slice()
        .reverse()
        .map(
          (l) => `<div class="log">
            <span>${new Date(l.created_at).toLocaleTimeString("ja-JP")}</span>
            <span class="action">${l.action}</span>
            <span>${l.actor}${l.payload.policy ? ` policy=${l.payload.policy}` : ""}${
            l.payload.amount_yen !== undefined ? ` ¥${l.payload.amount_yen}` : ""
          }${l.payload.status ? ` ${l.payload.status}` : ""}</span>
          </div>`
        )
        .join("")
    : '<span class="empty">監査ログはまだありません</span>';
}

$("inbox-select").onchange = loadInbox;

loadProviders();
loadUsers().then(loadChat);
