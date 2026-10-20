import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";
import { authRegistry } from "./auth-fixture.mjs";

let registry;
test.afterEach(async () => { await registry?.close(); registry = null; });

async function openRegistry(page, baseURL, options = {}) {
  registry = await authRegistry(baseURL, options);
  await page.addInitScript(() => {
    Object.defineProperty(globalThis, "showSaveFilePicker", { value: undefined });
  });
  if (options.configure !== false) {
    const config = options.wrongRealm
      ? { [registry.origin]: { ...registry.config[registry.origin], realm: `${registry.origin}/wrong` } }
      : registry.config;
    await page.addInitScript((value) => { globalThis.OCI_ZERO_ANONYMOUS_AUTH = value; }, config);
  }
  await page.goto(`${options.bundled ? "/dist/proxyless.html" : "/"}?isolated`);
  await expect(page.locator("#repository-presets button").first()).toBeAttached();
  expect(await page.evaluate(() => crossOriginIsolated)).toBe(true);
  await page.context().addCookies([{ url: registry.origin, name: "registry-cookie", value: "must-not-be-sent" }]);
  await page.locator("#repository-input").fill(`${registry.origin}/demo/image`);
  await page.getByRole("button", { name: "Browse tags" }).click();
}

for (const bundled of [false, true]) {
  test(`pulls with a CORS-hidden challenge in the ${bundled ? "single-file" : "module"} page`, async ({ page, baseURL }) => {
    await openRegistry(page, baseURL, { bundled });
    // A raw network response has the challenge; the normal browser cannot read it.
    const result = await page.evaluate(async (origin) => {
      const response = await fetch(`${origin}/v2/demo/image/manifests/multi`, { credentials: "omit" });
      return { status: response.status, challenge: response.headers.get("www-authenticate") };
    }, registry.origin);
    expect(result).toEqual({ status: 401, challenge: null });
    await page.getByRole("button", { name: "Open version multi" }).click();
    await page.getByRole("button", { name: /Select platform linux\/amd64/ }).click();
    await expect(page.locator("#status")).toHaveText("Ready: 1 verified image layer.");
    await page.locator("#scan-files").click();
    await expect(page.locator("#status")).toHaveText("Scanned 1 layer.");
    const row = page.getByRole("row").filter({ hasText: "etc/hello.txt" });
    const downloadPromise = page.waitForEvent("download");
    await row.getByRole("button", { name: "Download etc/hello.txt" }).click();
    const download = await downloadPromise;
    expect(await readFile(await download.path(), "utf8")).toBe("hello from the streamed layer\n");
    expect(registry.issued).toBe(1);
    expect(registry.requests.some((r) => r.method === "OPTIONS" && r.preflightHeaders.includes("authorization"))).toBe(true);
    expect(registry.requests.every((r) => r.cookie === undefined)).toBe(true);
    expect(registry.requests.filter((r) => r.path === "/20180419/docker/token").map((r) => r.query)).toEqual([
      { service: "fixture-registry", scope: "repository:demo/image:pull" },
    ]);
  });
}

for (const status of [401, 403]) {
  test(`refreshes a cached token once after HTTP ${status}`, async ({ page, baseURL }) => {
    await openRegistry(page, baseURL);
    await expect(page.getByRole("button", { name: "Open version multi" })).toBeVisible();
    // Wait for progressive manifest work before revoking its cached token.
    await expect(page.locator("#tag-limit")).toContainText("1 of 1 loaded tags resolved");
    registry.token = null;
    registry.revokedStatus = status;
    await page.getByRole("button", { name: "Browse tags" }).click();
    await expect.poll(() => registry.issued).toBe(2);
    await expect(page.locator("#status")).toHaveText("Loaded 1 tag for demo/image.");
    await expect(page.locator("#tag-limit")).toContainText("1 of 1 loaded tags resolved");
    registry.rejectFresh = true;
    await page.getByRole("button", { name: "Browse tags" }).click();
    await expect(page.locator("#status")).toContainText(`Tags returned HTTP ${status}`);
    expect(registry.issued).toBe(3); // No unbounded retry of a rejected fresh token.
  });
}

test("renews an expired token before using it", async ({ page, baseURL }) => {
  await openRegistry(page, baseURL);
  await expect(page.locator("#tag-limit")).toContainText("1 of 1 loaded tags resolved");
  await page.clock.setFixedTime(Date.now() + 3_600_000);
  await page.getByRole("button", { name: "Browse tags" }).click();
  await expect.poll(() => registry.issued).toBe(2);
  await expect(page.locator("#status")).toHaveText("Loaded 1 tag for demo/image.");
});

for (const kind of ["manifest", "config"]) {
  test(`rejects a corrupt ${kind} after anonymous authentication`, async ({ page, baseURL }) => {
    await openRegistry(page, baseURL);
    await expect(page.locator("#tag-limit")).toContainText("1 of 1 loaded tags resolved");
    registry.corrupt = kind;
    await page.getByRole("button", { name: "Open version multi" }).click();
    await page.getByRole("button", { name: /Select platform linux\/amd64/ }).click();
    await expect(page.locator("#status")).toContainText("Digest mismatch");
    await expect(page.locator("#scan-files")).toBeHidden();
  });
}

test("prefers a visible Bearer challenge over configured fallback auth", async ({ page, baseURL }) => {
  await openRegistry(page, baseURL, { exposeChallenge: true, wrongRealm: true });
  await expect(page.getByRole("button", { name: "Open version multi" })).toBeVisible();
  expect(registry.issued).toBe(1);
  expect(registry.requests.some((r) => r.path === "/wrong")).toBe(false);
});

test("does not override a visible Basic challenge", async ({ page, baseURL }) => {
  await openRegistry(page, baseURL, { exposeChallenge: true, basic: true });
  await expect(page.locator("#status")).toContainText("Registry requires private/Basic credentials");
  expect(registry.issued).toBe(0);
});

test("preserves challenge discovery without explicit configuration", async ({ page, baseURL }) => {
  await openRegistry(page, baseURL, { exposeChallenge: true, credentialed: true, configure: false });
  await expect(page.getByRole("button", { name: "Open version multi" })).toBeVisible();
  expect(registry.issued).toBe(1);
});

test("explains an unconfigured registry's hidden challenge", async ({ page, baseURL }) => {
  await openRegistry(page, baseURL, { credentialed: true, configure: false });
  await expect(page.locator("#status")).toContainText("challenge may be hidden by CORS");
  await expect(page.locator("#status")).toContainText("OCI_ZERO_ANONYMOUS_AUTH");
  expect(registry.issued).toBe(0);
});

test("scopes fallback tokens to each request's repository", async ({ page, baseURL }) => {
  await openRegistry(page, baseURL);
  await expect(page.locator("#tag-limit")).toContainText("1 of 1 loaded tags resolved");
  await page.locator("#repository-input").fill(`${registry.origin}/demo/many`);
  await page.getByRole("button", { name: "Browse tags" }).click();
  await expect(page.locator("#status")).toHaveText("Loaded 1 tag for demo/many.");
  await expect(page.locator("#tag-limit")).toContainText("1 of 1 loaded tags resolved");
  expect(registry.requests.filter((r) => r.path === "/20180419/docker/token").map((r) => r.query.scope)).toEqual([
    "repository:demo/image:pull", "repository:demo/many:pull",
  ]);
});

test("does not apply anonymous configuration to another origin", async ({ page, baseURL }) => {
  await openRegistry(page, baseURL);
  await expect(page.locator("#tag-limit")).toContainText("1 of 1 loaded tags resolved");
  const other = await authRegistry(baseURL, { credentialed: true });
  try {
    await page.locator("#repository-input").fill(`${other.origin}/demo/image`);
    await page.getByRole("button", { name: "Browse tags" }).click();
    await expect(page.locator("#status")).toContainText("challenge may be hidden by CORS");
    expect(other.issued).toBe(0);
    expect(other.requests.every((r) => !r.authorization)).toBe(true);
  } finally {
    await other.close();
  }
});

test("live OCIR metadata with normal browser security", async ({ page }) => {
  test.skip(!process.env.OCI_ZERO_LIVE_OCIR, "set OCI_ZERO_LIVE_OCIR=1 to check public OCIR metadata");
  await page.goto("/?isolated");
  await expect(page.locator("#repository-presets button").first()).toBeAttached();
  await page.locator("#repository-input").fill("iad.ocir.io/weblogick8s/weblogic-operator-tutorial-store");
  await page.getByRole("button", { name: "Browse tags" }).click();
  await page.getByRole("button", { name: "Open version 1.0", exact: true }).click();
  const platform = page.getByRole("button", { name: /Select platform linux\/amd64/ });
  if (await platform.count()) await platform.first().click();
  await expect(page.locator("#status")).toContainText("Ready:", { timeout: 30_000 });
});
