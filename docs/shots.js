/**
 * トップページ（web/index.html）の「使い方」に載せる画面の写しを撮る。
 *
 *   TRANSIT_PROVIDER=mock LLM_PROVIDER=stub \
 *     docker compose --profile shots up --build shots
 *
 * 実APIのまま撮ると、撮り直すたびに数字が変わって紹介ページの文章と食い違う。
 * モックは固定値なので、ページに書いた「69分」とそのまま合う。
 *
 * 利用者と同じ順に操作して撮るので、画面を変えたら撮り直すだけで追随する。
 * 出力は web/shots/*.png。index.html からは /static/shots/ で参照する。
 */

const fs = require("fs");
const puppeteer = require("puppeteer");

const BASE = process.env.BASE_URL || "http://api:8080";
const OUT = process.env.OUT_DIR || "/work/web/shots";

// 紹介ページの枠は 16:10。切り取られる前提だが、近い比で撮っておくと寄りが自然になる。
const WIDE = { width: 1120, height: 700 };
// 1カラムの面（地図→様子→名前）は 1080px 以下でしか出ない。
const NARROW = { width: 900, height: 640 };

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// 6人。トップページの路線図と同じ顔ぶれにして、図と写しを一致させる。
const MEMBERS = [
  ["ゆい", "立川"],
  ["けん", "三鷹"],
  ["みなと", "横浜"],
  ["さくら", "大宮"],
  ["りく", "船橋"],
  ["あおい", "千葉"],
];

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await puppeteer.launch({
    executablePath: process.env.CHROME_BIN || "/usr/bin/chromium-browser",
    args: [
      "--no-sandbox",
      "--disable-dev-shm-usage",
      "--font-render-hinting=none",
      // 地図は WebGL。GPU の無いコンテナでは描けずに略図へ落ちるので、
      // ソフトウェア実装を明示して有効にする。
      "--use-gl=angle",
      "--use-angle=swiftshader",
      "--enable-unsafe-swiftshader",
    ],
  });
  const page = await browser.newPage();
  page.on("console", (m) => { if (m.type() === "error") console.log(`[画面] ${m.text()}`); });
  page.on("pageerror", (e) => console.log(`[画面] ${e.message}`));

  const shot = async (name, viewport, clip) => {
    await page.setViewport({ ...viewport, deviceScaleFactor: 2 });
    await sleep(600);
    await page.screenshot({ path: `${OUT}/${name}.png`, ...(clip ? { clip } : {}) });
    console.log(`撮影: ${name}.png`);
  };

  // 地図は外のタイルを読む。読めたか諦めたかが決まるまで待つ（最長12秒）。
  const waitForMap = async () => {
    await page
      .waitForFunction(
        () => document.querySelector("#map canvas") || document.querySelector("#map svg"),
        { timeout: 12000 }
      )
      .catch(() => console.log("地図が出なかった。略図のまま撮る。"));
    await sleep(1800); // タイルとカメラ移動が落ち着くまで
  };

  await page.setViewport({ ...WIDE, deviceScaleFactor: 2 });
  await page.goto(`${BASE}/app`, { waitUntil: "networkidle0" });
  await page.waitForSelector("#request-form", { timeout: 20000 });

  // --- 1枚目: 名前と出発駅を入れる ---
  // 既定は3行。6人ぶんに増やしてから入れる。
  for (let i = 3; i < MEMBERS.length; i += 1) await page.click("#rq-add");
  await page.evaluate((members) => {
    const names = [...document.querySelectorAll(".member-name")];
    const stations = [...document.querySelectorAll(".member-station")];
    members.forEach(([name, station], i) => {
      names[i].value = name;
      stations[i].value = station;
    });
    // 書いてある例文（placeholder）と同じにすると、入れた字なのか見本なのか分からない
    document.getElementById("rq-notes").value = "いちばん遠い人がつらくならないように";
    // 入力欄の縁取りが残ると「途中」に見える
    document.activeElement?.blur();
  }, MEMBERS);
  // 入力の面は縦に伸びる。「候補地を出す」まで写らないと使い方にならないので、
  // 枠を測り、その周りだけを切り出す（広い画面のままだと左右が余白だらけになる）。
  const cardHeight = await page.evaluate(
    () => Math.ceil(document.querySelector(".modal-card").getBoundingClientRect().height)
  );
  // 枠が縦に収まる画面にしてから測り直す。高さが変われば置かれる位置も変わる。
  await page.setViewport({ width: WIDE.width, height: cardHeight + 160, deviceScaleFactor: 2 });
  await sleep(600);
  const card = await page.evaluate(() => {
    const r = document.querySelector(".modal-card").getBoundingClientRect();
    const pad = 28;
    return {
      x: Math.max(0, Math.floor(r.x - pad)),
      y: Math.max(0, Math.floor(r.y - pad)),
      width: Math.ceil(r.width + pad * 2),
      height: Math.ceil(r.height + pad * 2),
    };
  });
  await page.screenshot({ path: `${OUT}/step1.png`, clip: card });
  console.log("撮影: step1.png");

  // --- 2枚目: 基準を切り替えて見比べる ---
  await page.click("#request-form button[type=submit]");
  await page.waitForFunction(() => document.querySelectorAll(".cand").length > 0, {
    timeout: 30000,
  });
  await waitForMap();
  await shot("step2", WIDE);

  // --- 3枚目: 地図と顔で受け取る ---
  // 狭い画面では候補を押すと、地図・様子・内訳がひとつの面にまとまる。
  await page.setViewport({ ...NARROW, deviceScaleFactor: 2 });
  await sleep(800);
  await page.click(".cand");
  await page.waitForFunction(() => document.body.dataset.sheet === "detail", { timeout: 5000 });
  await waitForMap();
  await shot("step3", NARROW);

  await browser.close();
}

main().catch((err) => {
  console.error(err.message);
  process.exit(1);
});
