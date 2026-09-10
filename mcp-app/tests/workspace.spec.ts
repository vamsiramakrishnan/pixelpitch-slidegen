import { expect, test } from "@playwright/test";
import { writeFile } from "node:fs/promises";

test("labels simulation and lets developers test a narrow chat column", async ({ page }, testInfo) => {
  await page.goto("/");
  await expect(page.locator("#preview-mode")).toContainText("Simulation: no model calls");
  const app = page.frameLocator("#app");
  await expect(app.getByLabel("What is the presentation about?")).toBeEditable();
  await page.getByLabel("Chat width").selectOption("390");
  await expect(page.locator("#app")).toHaveCSS("max-width", "390px");
  expect(await app.locator("body").evaluate((body) => body.scrollWidth > body.clientWidth)).toBe(false);
  await page.screenshot({ path: testInfo.outputPath("developer-preview-narrow.png"), fullPage: true });
});

test("opens early, reveals drafts, reconnects and completes through the MCP bridge", async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const started = Date.now();
  await page.goto("/");
  const app = page.frameLocator("#app");
  await expect(app.getByLabel("What is the presentation about?")).toBeEditable();
  const usable = Date.now() - started;
  await app.getByLabel("What is the presentation about?").fill("Retail investment review");
  await app.getByLabel("Slides", { exact: true }).fill("3");
  await app.getByLabel("Brand", { exact: true }).selectOption("stripe");
  await expect(app.getByRole("button", { name: "Generate deck", exact: true })).toBeEnabled();
  await page.screenshot({ path: testInfo.outputPath("brief-desktop.png"), fullPage: true });
  const clicked = Date.now();
  await app.getByRole("button", { name: "Generate deck", exact: true }).click();
  await expect(app.getByRole("button", { name: "Cancel generation" })).toBeVisible();
  const accepted = Date.now() - clicked;
  await expect(app.getByRole("button", { name: /The decision in front of us/ })).toBeVisible({ timeout: 20000 });
  const firstDraft = Date.now() - clicked;
  await expect(app.frameLocator("#preview").getByRole("heading", { name: "The decision in front of us" })).toBeVisible();
  await expect(app.frameLocator("#preview").getByText("Browser test fixture · 01", { exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("draft-desktop.png"), fullPage: true });
  await page.getByRole("button", { name: "Reconnect app" }).click();
  await expect(app.getByRole("heading", { name: "Retail investment review" })).toBeVisible();
  await page.reload();
  await expect(app.getByRole("heading", { name: "Retail investment review" })).toBeVisible();
  await expect(app.getByRole("button", { name: "Download PowerPoint" })).toBeVisible({ timeout: 25000 });
  const completed = Date.now() - clicked;
  await expect(app.getByText("3 exported · 0 drafts", { exact: true })).toBeVisible();
  await expect(app.locator("#preview-kind")).toContainText("Actual PowerPoint export");
  await app.getByRole("button", { name: /What needs to change/ }).click();
  await expect(app.frameLocator("#preview").getByRole("img", { name: "What needs to change" })).toBeVisible();
  await expect(app.locator("#preview")).toHaveAttribute("srcdoc", /data:image\/png/);
  await expect(app.locator("#preview")).toHaveAttribute("sandbox", "");
  await page.screenshot({ path: testInfo.outputPath("result-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByLabel("Theme").selectOption("dark");
  await expect(app.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(app.locator('#slides button[aria-current="true"]')).toHaveCSS("background-color", "rgb(41, 42, 45)");
  await page.screenshot({ path: testInfo.outputPath("result-mobile-dark.png"), fullPage: true });
  const overflow = await app.locator("body").evaluate((body) => body.scrollWidth > body.clientWidth);
  expect(overflow).toBe(false);
  expect(errors).toEqual([]);
  const timingPath = testInfo.outputPath("latency.json");
  await writeFile(timingPath, JSON.stringify({ mode: "synthetic-author-render-real-mcp-adk-worker", usable_ms: usable, accepted_ms: accepted, first_draft_ms: firstDraft, completed_ms: completed }, null, 2));
  await testInfo.attach("latency.json", { path: timingPath, contentType: "application/json" });
});

test("cancels a running build and retains its brief", async ({ page }) => {
  await page.goto("/");
  const app = page.frameLocator("#app");
  await app.getByLabel("What is the presentation about?").fill("Cancellation test");
  await app.getByLabel("Brand", { exact: true }).selectOption("stripe");
  await app.getByRole("button", { name: "Generate deck", exact: true }).click();
  await app.getByRole("button", { name: "Cancel generation" }).click();
  await expect(app.getByRole("heading", { name: "Generation cancelled" })).toBeVisible();
  await app.getByRole("button", { name: "Edit brief" }).click();
  await expect(app.getByLabel("What is the presentation about?")).toHaveValue("Cancellation test");
});

test("failure is recoverable and never claims a download", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const app = page.frameLocator("#app");
  await app.getByLabel("What is the presentation about?").fill("Fail this controlled build");
  await app.getByLabel("Brand", { exact: true }).selectOption("stripe");
  await page.screenshot({ path: testInfo.outputPath("brief-mobile.png"), fullPage: true });
  await app.getByRole("button", { name: "Generate deck", exact: true }).click();
  await expect(app.getByRole("heading", { name: "Generation stopped" })).toBeVisible();
  await expect(app.getByRole("button", { name: "Download PowerPoint" })).toBeHidden();
  await page.screenshot({ path: testInfo.outputPath("error-mobile.png"), fullPage: true });
  await app.getByRole("button", { name: "Edit brief" }).click();
  await expect(app.getByLabel("What is the presentation about?")).toHaveValue("Fail this controlled build");
});

test("a missing signed link remains recoverable instead of breaking status parsing", async ({ page }) => {
  await page.goto("/");
  const app = page.frameLocator("#app");
  await app.getByLabel("What is the presentation about?").fill("No download link returned");
  await app.getByLabel("Slides", { exact: true }).fill("1");
  await app.getByLabel("Brand", { exact: true }).selectOption("stripe");
  await app.getByRole("button", { name: "Generate deck", exact: true }).click();
  await expect(app.getByRole("heading", { name: "Download unavailable" })).toBeVisible({ timeout: 20000 });
  await expect(app.getByText("Generation finished, but no download link was returned.", { exact: true })).toBeVisible();
  await expect(app.getByRole("button", { name: "Download PowerPoint" })).toBeHidden();
  await expect(app.getByRole("button", { name: "Edit brief" })).toBeEnabled();
});
