import { expect, test } from "@playwright/test";
import { API_BASE, mockApi } from "./helpers/routes";

test.use({ storageState: "./e2e/.auth/admin.json" });

test.describe("admin pipeline enrichment and embedding", () => {
  test("loads pipeline stats and trend content from the default pipeline state", async ({ page }) => {
    await mockApi(page);
    await page.goto("/admin/scraper", { waitUntil: "domcontentloaded" });

    await expect(page.getByRole("heading", { name: "Processing pipeline" })).toBeVisible();

    // Funnel hint describes the enrichment/embedding state surfaced by the mocked API.
    await expect(
      page.getByText("Pipeline complete: 2 items embedded and ready for search/recommendation."),
    ).toBeVisible();

    // Trend content records are listed and the "wild score" is visible.
    await expect(page.getByRole("heading", { name: "Trend content records" })).toBeVisible();

    const a1Row = page.getByRole("row", { name: /Fixture_A1/ });
    await expect(a1Row).toContainText("49.40");
    await expect(a1Row).toContainText("embedded");

    const b2Row = page.getByRole("row", { name: /Fixture_B2/ });
    await expect(b2Row).toContainText("15.70");
    await expect(b2Row).toContainText("enriched");
  });

  for (const { label, slug } of [
    { label: "Enrich", slug: "enrich" },
    { label: "Embed", slug: "embed" },
  ]) {
    test(`triggers the ${label.toLowerCase()} pipeline stage`, async ({ page }) => {
      await mockApi(page);
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

  test("shows enrichment and embedding details for a trend content record", async ({ page }) => {
    await mockApi(page);
    await page.goto("/admin/scraper", { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: "Trend content records" })).toBeVisible();

    const a1Row = page.getByRole("row", { name: /Fixture_A1/ });
    await expect(a1Row).toBeVisible();
    await a1Row.click();

    const dialog = page.getByRole("dialog");
    await expect(dialog.getByText("Post Fixture_A1")).toBeVisible();

    // Metrics & scoring section displays the wild score and its raw components.
    await expect(dialog.getByText("Viral score")).toBeVisible();
    await expect(dialog.getByText("49.4")).toBeVisible();
    await expect(dialog.getByText("raw_score")).toBeVisible();

    // Transcript and combined text are persisted and visible.
    await expect(dialog.getByText("Transcript", { exact: true })).toBeVisible();
    await expect(
      dialog.getByText("Welcome to another travel reel exploring hidden coastal towns.", { exact: true }),
    ).toBeVisible();

    // Embedding section displays the Qdrant vector reference.
    await expect(dialog.getByText("Embedding vector ID")).toBeVisible();
    await expect(dialog.getByText("embedding-vector-a1")).toBeVisible();
  });
});
