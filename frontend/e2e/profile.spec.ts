import { test, expect } from "@playwright/test";
import { mockApi } from "./helpers/routes";
import { regularUser } from "./helpers/fixtures";

test.describe("Creator profile", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page, { user: regularUser });
  });

  test("renders profile analytics, pillars, and recommendation history", async ({ page }) => {
    await page.goto("/profile");

    await expect(page.getByRole("heading", { name: "Your creator profile" })).toBeVisible();
    await expect(page.getByText("@fixture_creator")).toBeVisible();
    await expect(page.getByText("Profile ready")).toBeVisible();

    await expect(page.getByRole("heading", { name: "Profile analytics" })).toBeVisible();
    await expect(page.getByText("86%")).toBeVisible();
    await expect(page.getByText("Content pillars")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Travel" })).toBeVisible();
    await expect(page.getByText("Winning patterns")).toBeVisible();
    await expect(page.getByText("Opening with a question")).toBeVisible();

    await expect(page.getByRole("heading", { name: "Recommendation history" })).toBeVisible();
    await expect(page.getByText("Hidden Istanbul street food")).toBeVisible();
  });
});
