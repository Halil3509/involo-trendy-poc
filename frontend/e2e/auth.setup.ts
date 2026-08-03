import { mkdirSync } from "node:fs";
import { dirname } from "node:path";

import { test as setup } from "@playwright/test";

import { ADMIN_EMAIL, ADMIN_PASSWORD, loginAsAdmin } from "./helpers/auth";
import { mockApi } from "./helpers/routes";

setup("authenticate as admin", async ({ page }) => {
  await mockApi(page, {}, { authenticated: false });
  await page.goto("/login", { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("networkidle");
  await loginAsAdmin(page, ADMIN_EMAIL, ADMIN_PASSWORD);
  const storagePath = "./e2e/.auth/admin.json";
  mkdirSync(dirname(storagePath), { recursive: true });
  await page.context().storageState({ path: storagePath });
});
