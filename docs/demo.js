/**
 * 提出用のデモ動画を撮る。一連の基本操作を、人が触る速さで通しで見せる。
 *
 *   TRANSIT_PROVIDER=mock LLM_PROVIDER=stub \
 *     docker compose --profile shots run --rm --build demo
 *
 * 出力は docs/demo/mannaka-demo.mp4（リポジトリには入れない）。
 * 画面の写し（docs/shots.js）と同じく、実際に操作して撮るので、
 * 画面を変えたら撮り直すだけで追随する。モックで撮るのは、数字を毎回揃えるため。
 *
 * 流れ: 入力 → 候補地 → 基準の切り替え → 候補の切り替え
 * トップページとスマホ表示は入れない。アプリの基本操作だけを見せる。
 */

const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");
const puppeteer = require("puppeteer");

const BASE = process.env.BASE_URL || "http://api:8080";
const OUT = process.env.OUT_DIR || "/work/docs/demo";

// 動画は 16:9。3カラムが並ぶ幅で撮る。
const PC = { width: 1280, height: 720 };

// 6人。トップページの路線図・画面の写しと同じ顔ぶれ。
const MEMBERS = [
  ["ゆい", "立川"],
  ["けん", "三鷹"],
  ["みなと", "横浜"],
  ["さくら", "大宮"],
  ["りく", "船橋"],
  ["あおい", "千葉"],
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ------------------------------------------------------------ 画面に足すもの

/* ヘッドレスではマウスカーソルが映らない。どこを押したか分からない動画に
   なるので、ページ側に偽のカーソルと押した印を描く。 */
function installPointer() {
  const install = () => {
    if (document.getElementById("__pointer")) return;
    const layer = document.createElement("div");
    layer.id = "__pointer";
    layer.style.cssText =
      "position:fixed;inset:0;pointer-events:none;z-index:2147483647;overflow:hidden";
    const arrow = document.createElement("div");
    arrow.style.cssText =
      "position:absolute;left:0;top:0;width:24px;height:24px;transform:translate(-99px,-99px)";
    arrow.innerHTML =
      '<svg viewBox="0 0 24 24" width="24" height="24"><path d="M4 2 L4 19.5 L8.6 15.2 ' +
      'L11.8 22.2 L15 20.8 L11.8 13.9 L18 13.9 Z" fill="#20242b" stroke="#fff" ' +
      'stroke-width="1.6" stroke-linejoin="round"/></svg>';
    layer.append(arrow);
    document.documentElement.append(layer);

    document.addEventListener(
      "mousemove",
      (e) => (arrow.style.transform = `translate(${e.clientX - 4}px,${e.clientY - 2}px)`),
      true
    );
    document.addEventListener(
      "mousedown",
      (e) => {
        const dot = document.createElement("div");
        dot.style.cssText =
          `position:absolute;left:${e.clientX - 18}px;top:${e.clientY - 18}px;` +
          "width:36px;height:36px;border-radius:50%;border:3px solid #e5432e;" +
          "background:rgba(229,67,46,.18);transition:transform .45s ease-out,opacity .45s ease-out";
        layer.append(dot);
        requestAnimationFrame(() => {
          dot.style.transform = "scale(1.7)";
          dot.style.opacity = "0";
        });
        setTimeout(() => dot.remove(), 600);
      },
      true
    );
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", install);
  } else {
    install();
  }
}

/* 字幕。ナレーションの代わりに、いま何をしているかを一言だけ出す。
   ページを移っても消えないよう、出すたびに無ければ作る。 */
async function caption(page, text) {
  await page.evaluate(
    (text) => {
      let el = document.getElementById("__caption");
      if (!el) {
        el = document.createElement("div");
        el.id = "__caption";
        el.style.cssText =
          "position:fixed;left:50%;bottom:14px;transform:translateX(-50%);" +
          "z-index:2147483646;pointer-events:none;max-width:92vw;text-align:center;" +
          "background:rgba(32,36,43,.9);color:#fff;border-radius:999px;" +
          "font-family:'IBM Plex Sans JP','Noto Sans CJK JP',sans-serif;font-weight:700;" +
          "letter-spacing:.02em;transition:opacity .25s ease;opacity:0;" +
          "box-shadow:0 6px 20px rgba(32,36,43,.18)";
        document.documentElement.append(el);
      }
      el.style.fontSize = "18px";
      el.style.padding = "11px 26px";
      el.style.opacity = "0";
      setTimeout(() => {
        el.textContent = text;
        el.style.opacity = text ? "1" : "0";
      }, 200);
    },
    text
  );
  await sleep(300);
}

// ------------------------------------------------------------ 人の速さで触る

/* 指先の位置を覚えておき、そこから目的地まで滑らかに動かす。
   page.mouse.move の steps は一瞬で全部送るので、動いて見えない。 */
function pointer(page) {
  let at = { x: 640, y: 360 };

  const moveTo = async (x, y, ms = 450) => {
    const from = { ...at };
    const steps = Math.max(8, Math.round(ms / 16));
    for (let i = 1; i <= steps; i += 1) {
      const t = i / steps;
      const ease = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;
      await page.mouse.move(from.x + (x - from.x) * ease, from.y + (y - from.y) * ease);
      await sleep(16);
    }
    at = { x, y };
  };

  const click = async (selector, { pause = 250, scroll = true } = {}) => {
    const el = typeof selector === "string" ? await page.waitForSelector(selector) : selector;
    if (scroll) {
      // 画面外なら、飛ばずにゆっくり寄せる（どこへ行ったか分からなくなるため）
      const off = await el.evaluate((node) => {
        const r = node.getBoundingClientRect();
        return r.top < 60 || r.bottom > window.innerHeight - 60;
      });
      if (off) {
        await el.evaluate((node) => node.scrollIntoView({ block: "center", behavior: "smooth" }));
        await sleep(700);
      }
    }
    const box = await el.boundingBox();
    const x = box.x + box.width / 2;
    const y = box.y + Math.min(box.height / 2, 40);
    await moveTo(x, y);
    await sleep(pause);
    await page.mouse.click(x, y);
    await sleep(200);
  };

  const type = async (selector, text) => {
    await click(selector, { pause: 120 });
    await page.keyboard.type(text, { delay: 85 });
    await sleep(150);
  };

  // ページを移るとカーソルの絵は作り直され、画面外に置かれる。今の位置に戻す
  const reappear = async () => page.mouse.move(at.x + 1, at.y);

  return { moveTo, click, type, reappear };
}

// 地図は外のタイルを読む。枠が出ただけでは下地が白いので、タイルまで
// 読み終わるのを待ってから次へ進む（最長12秒。諦めて略図に落ちた場合もそこで進む）
async function waitForMap(page) {
  await page
    .waitForFunction(
      () =>
        typeof state !== "undefined" &&
        (state.mapDead || (state.map && state.map.loaded() && state.map.areTilesLoaded())),
      { timeout: 12000 }
    )
    .catch(() => console.log("地図の読み込みが終わらなかった。そのまま撮る。"));
  await sleep(1200);
}

async function topStation(page) {
  return page.$eval(".cand.top", (el) => el.dataset.station);
}

// ------------------------------------------------------------ PC

async function record(browser, file) {
  const page = await browser.newPage();
  await page.setViewport({ ...PC, deviceScaleFactor: 1 });
  await page.evaluateOnNewDocument(installPointer);
  const hand = pointer(page);

  await page.goto(`${BASE}/app`, { waitUntil: "networkidle0" });
  await page.waitForSelector("#request-form");
  await page.evaluate(() => document.fonts.ready);
  const rec = await page.screencast({ path: file });
  await hand.reappear();

  // --- 入力
  await caption(page, "マンナカ — N人の待ち合わせ場所を、公平に比べる");
  await sleep(2400);
  await caption(page, "① 参加者の名前と出発駅を入れる（ログイン不要）");
  await sleep(1000);
  for (let i = 0; i < MEMBERS.length; i += 1) {
    if (i >= 3) await hand.click("#rq-add", { pause: 150 });
    const [name, station] = MEMBERS[i];
    await hand.type(`.member:nth-child(${i + 1}) .member-name`, name);
    await hand.type(`.member:nth-child(${i + 1}) .member-station`, station);
  }
  await caption(page, "希望条件の文からも、どの基準で選ぶかを読み取る");
  await hand.type("#rq-notes", "いちばん遠い人がつらくならないように");
  await sleep(700);
  await hand.click("#request-form button[type=submit]", { pause: 450 });

  // --- 候補地
  await page.waitForFunction(() => document.querySelectorAll(".cand").length > 0, {
    timeout: 30000,
  });
  await caption(page, "② 候補地が出る。地図・候補・みんなの様子");
  await waitForMap(page);
  await sleep(2200);
  await caption(page, "負担のいちばん重い人は、赤くうなだれる");
  await hand.moveTo(1150, 420, 700);
  await sleep(2600);

  // --- 基準の切り替え（ここが見せ場）
  await caption(page, "③ 基準を切り替えると、集まる駅が変わる");
  await sleep(1400);
  for (const [key, label] of [
    ["sum", "早い"],
    ["cost", "安い"],
    ["minimax", "バランスよく"],
  ]) {
    await hand.click(`.ptab[data-policy=${key}]`, { pause: 300 });
    await page.waitForFunction(
      (k) => document.querySelector(`.ptab[data-policy=${k}]`)?.getAttribute("aria-selected") === "true",
      {},
      key
    );
    await sleep(700);
    await caption(page, `「${label}」なら → ${await topStation(page)}`);
    await sleep(2800);
  }

  // --- 候補の切り替え
  await caption(page, "④ 候補を押すと、地図も表情もその場所に変わる");
  await sleep(1200);
  await hand.click(".cand:nth-child(2) .cand-head", { pause: 300 });
  await sleep(3000);
  // 3つ目は画面の下端、字幕の真上に来てしまう。おすすめへ戻る操作で見せる
  await hand.click(".cand:nth-child(1) .cand-head", { pause: 300 });
  await sleep(3000);

  // 締め。決めるのではなく、負担を見えるようにするアプリだと言って終える
  await caption(page, "どの基準で選んでも、誰にどれだけ寄ったかが見える");
  await sleep(3400);
  await rec.stop();
  await page.close();
}

// ------------------------------------------------------------ 書き出す

function encode(src, outFile) {
  execFileSync(
    "ffmpeg",
    [
      "-y", "-loglevel", "error",
      "-i", src,
      // 撮影のフレーム間隔は揺れるので 30fps に揃える
      "-vf", "fps=30,setsar=1",
      // QuickTime でも YouTube でもそのまま開けるよう、H.264 / yuv420p にする
      "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
      "-movflags", "+faststart",
      outFile,
    ],
    { stdio: "inherit" }
  );
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await puppeteer.launch({
    executablePath: process.env.CHROME_BIN || "/usr/bin/chromium-browser",
    args: [
      "--no-sandbox",
      "--disable-dev-shm-usage",
      "--font-render-hinting=none",
      // 地図は WebGL。GPU の無いコンテナでもソフトウェアで描かせる
      "--use-gl=angle",
      "--use-angle=swiftshader",
      "--enable-unsafe-swiftshader",
    ],
  });

  const rawFile = path.join(OUT, "_raw.webm");
  const outFile = path.join(OUT, "mannaka-demo.mp4");

  try {
    console.log("撮影中…");
    await record(browser, rawFile);
  } finally {
    await browser.close();
  }

  console.log("書き出し中…");
  encode(rawFile, outFile);
  fs.rmSync(rawFile);
  console.log(`出力: ${path.relative("/work", outFile)}`);
}

main().catch((err) => {
  console.error(err.stack || err.message);
  process.exit(1);
});
