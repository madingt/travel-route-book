#!/usr/bin/env node
/* Check a built route book before publishing.
 *
 * Usage: node test_routebook.cjs routebook.html
 *   (if playwright is installed globally: NODE_PATH=$(npm root -g) node test_routebook.cjs routebook.html)
 *
 * Checks:
 *  1. JS on: only one section visible; every tab switches pages.
 *  2. Day cards on the overview open their day; pager prev/next works.
 *  3. A host that hijacks <a href="#..."> clicks (like the Artifact viewer) can't break navigation.
 *  4. JS off: every section is visible (offline/phone-preview fallback).
 *  5. 390px wide: no horizontal page overflow.
 *  6. External map links open in a new tab.
 * Exits non-zero on the first failure.
 */
const path = require("path");
const { chromium } = require("playwright");

const file = process.argv[2];
if (!file) { console.error("usage: node test_routebook.cjs routebook.html"); process.exit(2); }
const url = "file://" + path.resolve(file);
const fail = (msg) => { console.error("FAIL: " + msg); process.exit(1); };
const ok = (msg) => console.log("ok   " + msg);

async function visibleSections(page) {
  return page.$$eval("section[data-p]", (ss) => ss.filter((s) => !s.hidden).map((s) => s.id));
}

(async () => {
  const launchOpts = {};
  if (process.env.CHROMIUM_PATH) launchOpts.executablePath = process.env.CHROMIUM_PATH;
  const browser = await chromium.launch(launchOpts);

  // 1-3, 6: JS on
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  // Simulate a host that swallows hash-link clicks (capture phase on window, before the page).
  await page.addInitScript(() => {
    window.addEventListener("click", (e) => {
      const a = e.target.closest && e.target.closest('a[href^="#"]');
      if (a) { e.preventDefault(); }
    }, true);
  });
  await page.goto(url);
  const all = await page.$$eval("section[data-p]", (ss) => ss.map((s) => s.id));
  if (all.length < 2) fail("fewer than 2 sections");
  let vis = await visibleSections(page);
  if (vis.length !== 1) fail(`expected 1 visible section on load, got ${vis.length}`);
  ok(`${all.length} sections, one visible on load (${vis[0]})`);

  const leftoverHash = await page.$$eval('a[href^="#"]', (as) => as.length);
  if (leftoverHash) fail(`${leftoverHash} internal #links were not converted`);
  ok("internal #links converted to buttons");

  const tabs = await page.$$eval("nav.tabs [data-go]", (ts) => ts.map((t) => t.getAttribute("data-go")));
  for (const id of tabs) {
    await page.click(`nav.tabs [data-go="${id}"]`);
    vis = await visibleSections(page);
    if (vis.length !== 1 || vis[0] !== id) fail(`tab ${id} -> visible ${vis.join(",")}`);
  }
  ok(`all ${tabs.length} tabs switch pages (with hash-link hijacking active)`);

  await page.click('nav.tabs [data-go="overview"]');
  const cards = await page.$$eval("#overview .daycard[data-go]", (cs) => cs.map((c) => c.getAttribute("data-go")));
  for (const id of cards) {
    await page.click(`#overview .daycard[data-go="${id}"]`);
    if ((await visibleSections(page))[0] !== id) fail(`day card ${id} did not open its day`);
    await page.click('nav.tabs [data-go="overview"]');
  }
  ok(`${cards.length} day cards open their day`);

  if (cards.length > 1) {
    await page.click(`nav.tabs [data-go="${cards[0]}"]`);
    await page.click(`#${cards[0]} .pager [data-go="${cards[1]}"]`);
    if ((await visibleSections(page))[0] !== cards[1]) fail("pager next failed");
    await page.click(`#${cards[1]} .pager [data-go="${cards[0]}"]`);
    if ((await visibleSections(page))[0] !== cards[0]) fail("pager prev failed");
    ok("pager prev/next works");
  }

  await page.keyboard.press("Tab");
  const badExt = await page.$$eval('a[href^="http"]', (as) => as.filter((a) => a.target !== "_blank").length);
  if (badExt) fail(`${badExt} external links without target=_blank`);
  ok("external links open in a new tab");

  for (const id of all) {
    await page.evaluate((sid) => {
      document.querySelectorAll("section[data-p]").forEach((s) => (s.hidden = s.id !== sid));
    }, id);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    if (overflow > 1) fail(`section ${id} overflows horizontally by ${overflow}px at 390px`);
  }
  ok("no horizontal overflow at 390px");
  if (errors.length) fail("page errors: " + errors.join(" | "));
  ok("no page errors");
  await ctx.close();

  // 4: JS off
  const ctx2 = await browser.newContext({ javaScriptEnabled: false });
  const p2 = await ctx2.newPage();
  await p2.goto(url);
  const vis2 = await p2.$$eval("section[data-p]", (ss) =>
    ss.filter((s) => getComputedStyle(s).display !== "none").length);
  if (vis2 !== all.length) fail(`JS off: ${vis2}/${all.length} sections visible`);
  ok("JS off: every section visible");
  await ctx2.close();

  await browser.close();
  console.log("PASS");
})().catch((e) => fail(e.stack || String(e)));
