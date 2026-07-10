const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const BASE_URL = process.env.AGENDASALON_BASE_URL || "http://127.0.0.1:8000";
const OUT_DIR = path.join(__dirname, "..", "_visual_checks", "auth");

const pages = [
  {
    label: "professional-login",
    url: "/cuenta/entrar/",
    expectedImage: "agendasalon-internal-login-bg.png",
  },
  {
    label: "customer-mari-login",
    url: "/clientes/peluqueria-mari/entrar/",
    expectedImage: "customer-login-peluqueria-mari-bg.png",
  },
  {
    label: "customer-mari-register",
    url: "/clientes/peluqueria-mari/registro/",
    expectedImage: "customer-login-peluqueria-mari-bg.png",
  },
  {
    label: "customer-barberia-login",
    url: "/clientes/barberia-norte/entrar/",
    expectedImage: "customer-login-barberia-norte-bg-v2.png",
  },
  {
    label: "customer-barberia-register",
    url: "/clientes/barberia-norte/registro/",
    expectedImage: "customer-login-barberia-norte-bg-v2.png",
  },
  {
    label: "booking-mari",
    url: "/reservar/peluqueria-mari/",
    expectedImage: "customer-login-peluqueria-mari-bg.png",
  },
  {
    label: "booking-barberia",
    url: "/reservar/barberia-norte/",
    expectedImage: "customer-login-barberia-norte-bg-v2.png",
  },
];

const viewports = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "mobile", width: 390, height: 844 },
];

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function pageUrl(url) {
  return new URL(url, BASE_URL).toString();
}

async function inspect(page, item, viewportName) {
  await page.goto(pageUrl(item.url), { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("load", { timeout: 10000 }).catch(() => {});

  const screenshotPath = path.join(OUT_DIR, `${viewportName}-${item.label}.png`);
  await page.screenshot({ path: screenshotPath, fullPage: viewportName === "mobile" });

  return page.evaluate(
    ({ label, expectedImage, screenshotPath }) => {
      const loginPage = document.querySelector("main.login-page");
      const visualRoot = loginPage || document.body;
      const content = document.querySelector(".client-auth-content, .internal-auth-content");
      const panel = document.querySelector(".login-panel");
      const hero = document.querySelector(".login-hero-copy");
      const imageSpace = document.querySelector(".client-auth-image-space, .internal-auth-image-space");
      const h1 = document.querySelector("h1");
      const brand = document.querySelector(".login-brand strong");
      const style = visualRoot ? window.getComputedStyle(visualRoot) : null;
      const rect = (element) => {
        if (!element) return null;
        const r = element.getBoundingClientRect();
        return {
          x: Math.round(r.x),
          y: Math.round(r.y),
          width: Math.round(r.width),
          height: Math.round(r.height),
          right: Math.round(r.right),
          bottom: Math.round(r.bottom),
        };
      };
      const contentRect = rect(content);
      const imageRect = rect(imageSpace);
      return {
        label,
        title: document.title,
        finalUrl: window.location.href,
        className: visualRoot ? visualRoot.className : "",
        brand: brand ? brand.textContent.trim() : "",
        h1: h1 ? h1.textContent.trim().replace(/\s+/g, " ") : "",
        backgroundImage: style ? style.backgroundImage : "",
        expectedImage,
        expectedImageLoaded: style ? style.backgroundImage.includes(expectedImage) : false,
        hasLoginPage: Boolean(loginPage),
        hasHorizontalOverflow:
          document.documentElement.scrollWidth > document.documentElement.clientWidth,
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
        scrollHeight: document.documentElement.scrollHeight,
        clientHeight: document.documentElement.clientHeight,
        contentRect,
        heroRect: rect(hero),
        panelRect: rect(panel),
        imageRect,
        contentBeforeImage:
          !contentRect || !imageRect || imageRect.width === 0 || contentRect.right <= imageRect.x,
        screenshotPath,
      };
    },
    { label: item.label, expectedImage: item.expectedImage, screenshotPath }
  );
}

(async () => {
  ensureDir(OUT_DIR);
  const browser = await chromium.launch();
  const results = [];

  try {
    for (const viewport of viewports) {
      const context = await browser.newContext({
        viewport: { width: viewport.width, height: viewport.height },
        deviceScaleFactor: 1,
      });
      const page = await context.newPage();
      for (const item of pages) {
        results.push({
          viewport: viewport.name,
          ...(await inspect(page, item, viewport.name)),
        });
      }
      await context.close();
    }
  } finally {
    await browser.close();
  }

  const reportPath = path.join(OUT_DIR, "report.json");
  fs.writeFileSync(reportPath, JSON.stringify(results, null, 2));
  console.log(JSON.stringify({ reportPath, results }, null, 2));
})();
