import { test, expect } from "@playwright/test";
import { API_BASE, mockApi } from "./helpers/routes";

test.describe("Admin scraper and pipeline", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
  });

  test("loads config, adds a keyword, and saves", async ({ page }) => {
    await page.goto("/admin/scraper", { waitUntil: "domcontentloaded" });

    await expect(page.getByRole("heading", { name: "Configuration" })).toBeVisible();
    await expect(page.getByText("fashion")).toBeVisible();

    const keywordInput = page.getByLabel("Discovery keywords");
    await keywordInput.fill("sustainable style");
    await keywordInput.press("Enter");
    await expect(page.getByText("sustainable style")).toBeVisible();
    await expect(page.getByText("Pipeline complete:")).toBeVisible();

    const savePromise = page.waitForRequest(`${API_BASE}/api/v1/admin/scraper/config`);
    await page.getByRole("button", { name: "Save configuration" }).click();
    const request = await savePromise;

    expect(request.method()).toBe("PUT");
    expect(await request.postDataJSON()).toMatchObject({
      keywords: ["fashion", "sustainable style"],
      reels_per_keyword: 12,
      headless: true,
      viral_threshold: 20,
      schedule_cron: "0 5 * * *",
    });

    await expect(page.getByText("Scraper configuration saved.")).toBeVisible();
  });

  test("rejects invalid cron and starts a scrape", async ({ page }) => {
    await page.goto("/admin/scraper", { waitUntil: "domcontentloaded" });

    await page.getByLabel("Schedule (cron)").fill("90 * * * *");
    await expect(page.getByText("Pipeline complete:")).toBeVisible();
    await page.getByRole("button", { name: "Save configuration" }).click();
    await expect(page.getByText("Schedule must be a 5-field cron expression (or empty).")).toBeVisible();

    const startPromise = page.waitForRequest(`${API_BASE}/api/v1/admin/scraper/runs`);
    await page.getByRole("button", { name: "Start scrape" }).click();
    const request = await startPromise;

    expect(request.method()).toBe("POST");
    await expect(page.getByText("Scraper run started.")).toBeVisible();
  });

  for (const { label, slug } of [
    { label: "Enrich", slug: "enrich" },
    { label: "Embed", slug: "embed" },
  ]) {
    test(`starts the ${label.toLowerCase()} pipeline stage`, async ({ page }) => {
      await page.goto("/admin/scraper", { waitUntil: "domcontentloaded" });

      await expect(page.getByRole("heading", { name: "Processing pipeline" })).toBeVisible();
      await expect(page.getByText("Pipeline complete:")).toBeVisible();

      const requestPromise = page.waitForRequest(
        `${API_BASE}/api/v1/admin/pipeline/${slug}`,
      );
      await page.getByRole("button", { name: label }).click();
      const request = await requestPromise;

      expect(request.method()).toBe("POST");
      await expect(page.getByText(`${label} job started.`)).toBeVisible();
    });
  }
});
