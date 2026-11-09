const { chromium } = require('../../../frontend/node_modules/playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const report = { checks: [], errors: [], layouts: [] };
  const check = (name, ok, details) => {
    report.checks.push({ name, ok, details });
    if (!ok) report.errors.push(name);
  };
  const layout = async (page, label, desktop) => {
    const data = await page.evaluate(() => ({
      width: innerWidth,
      height: innerHeight,
      docWidth: document.documentElement.scrollWidth,
      docHeight: document.documentElement.scrollHeight,
      regions: [
        ...document.querySelectorAll(
          '.sources,.editor,.inspector,.statusbar,.tab-panel,.inspector-footer',
        ),
      ]
        .filter((n) => !n.hidden && getComputedStyle(n).display !== 'none')
        .map((n) => {
          const r = n.getBoundingClientRect();
          return {
            class: n.className,
            top: r.top,
            bottom: r.bottom,
            left: r.left,
            right: r.right,
            height: r.height,
          };
        }),
    }));
    report.layouts.push({ label, ...data });
    check(`${label}: no horizontal overflow`, data.docWidth <= data.width + 1);
    if (desktop) {
      check(`${label}: no vertical overflow`, data.docHeight <= data.height + 1);
      check(
        `${label}: regions in viewport`,
        data.regions.every((r) => r.bottom <= data.height + 1 && r.top >= 0),
      );
      const p = data.regions.find((r) => r.class === 'tab-panel'),
        f = data.regions.find((r) => r.class === 'inspector-footer');
      if (p && f)
        check(`${label}: inspector does not overlap`, p.bottom <= f.top + 1, {
          panel: p.bottom,
          footer: f.top,
        });
    }
  };
  try {
    for (const theme of ['studio', 'precision', 'focus']) {
      const context = await browser.newContext();
      const page = await context.newPage();
      page.on('pageerror', (e) => report.errors.push(`${theme}: ${e.message}`));
      page.on('response', (r) => {
        if (r.status() >= 400) report.errors.push(`${r.status()} ${r.url()}`);
      });
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.goto(`http://127.0.0.1:8795/${theme}.html`);
      await page.evaluate(() => document.fonts.ready);
      await page.screenshot({ path: path.join(__dirname, 'shots', `${theme}-1440.png`) });
      await layout(page, `${theme}-1440`, true);
      await page.setViewportSize({ width: 1024, height: 600 });
      for (const tab of ['banner', 'filters', 'publish']) {
        await page.locator(`[data-tab="${tab}"]`).click();
        await layout(page, `${theme}-1024-${tab}`, true);
        await page.screenshot({ path: path.join(__dirname, 'shots', `${theme}-1024-${tab}.png`) });
      }
      await page.locator('[data-source="1"]').click();
      check(
        `${theme}: source switch`,
        (await page.locator('#viewer-file').innerText()) === 'episode_02.mp4',
      );
      await page.locator('[data-tab="banner"]').click();
      await page.locator('[data-setting="width"]').fill('60');
      check(
        `${theme}: banner width`,
        (await page.locator('#banner-overlay').evaluate((n) => n.style.width)) === '60%',
      );
      await page.locator('[data-action="appearance"]').click();
      await page.locator('[data-setting="text"]').fill('Проверка баннера');
      check(
        `${theme}: banner copy`,
        (await page.locator('#banner-overlay').innerText()) === 'Проверка баннера',
      );
      await page.keyboard.press('Escape');
      check(
        `${theme}: dialog closes and restores focus`,
        (await page.evaluate(() => document.activeElement.dataset.action)) === 'appearance',
      );
      await page.locator('[data-tab="filters"]').click();
      await page.locator('[data-preset="warm"]').click();
      check(
        `${theme}: preset applies`,
        (await page.locator('#poster').evaluate((n) => n.style.filter)).includes('sepia(0.25)'),
      );
      await page.locator('[data-tab="banner"]').click();
      await page.keyboard.press('ArrowRight');
      check(
        `${theme}: keyboard tabs`,
        (await page.locator('#tab-filters').getAttribute('aria-selected')) === 'true',
      );
      await page.locator('[data-tab="banner"]').click();
      await page.locator('[data-action="play"]').click();
      await page.waitForFunction(() => !document.querySelector('#video').paused);
      check(
        `${theme}: actual video plays`,
        await page.locator('#video').evaluate((n) => !n.paused),
      );
      await page.locator('[data-action="play"]').click();
      await page.locator('[data-action="launch"]').click();
      await page.locator('#schedule-visibility').selectOption('unlisted');
      await page.locator('[data-action="demo-launch"]').click();
      check(
        `${theme}: invalid visibility explains recovery`,
        await page.locator('#dialog-error').isVisible(),
      );
      await page.locator('#schedule-visibility').selectOption('private');
      await page.locator('[data-action="demo-launch"]').click();
      check(
        `${theme}: demo queue`,
        await page
          .locator('#section-view')
          .innerText()
          .then((s) => s.includes('18 вариантов')),
      );
      await page.locator('[data-nav="editor"]').click();
      for (const size of [
        { width: 768, height: 1024 },
        { width: 390, height: 844 },
        { width: 320, height: 740 },
      ]) {
        await page.setViewportSize(size);
        await page.locator('[data-tab="banner"]').click();
        await layout(page, `${theme}-${size.width}`, false);
        if (size.width === 390)
          await page.screenshot({
            path: path.join(__dirname, 'shots', `${theme}-390.png`),
            fullPage: true,
          });
      }
      await context.close();
    }
  } finally {
    await browser.close();
    fs.writeFileSync(path.join(__dirname, 'verification.json'), JSON.stringify(report, null, 2));
    console.log(
      JSON.stringify(
        { checks: report.checks.length, errors: report.errors, layouts: report.layouts.length },
        null,
        2,
      ),
    );
  }
  if (report.errors.length) process.exitCode = 1;
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
