import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { test, expect } from "@playwright/test";
import { ADMIN_EMAIL, ADMIN_PASSWORD, loginAsAdmin } from "./helpers/auth";
import { mockApi } from "./helpers/routes";

test("debug authenticate as admin", async ({ page }) => {
  page.on("framenavigated", (frame) =>
    console.log(`[nav] ${frame.url()}`, frame === page.mainFrame() ? "main" : "frame"),
  );
  page.on("request", (request) =>
    console.log(`[req] ${request.method()} ${request.url()}`),
  );
  page.on("response", (response) =>
    console.log(`[res] ${response.status()} ${response.url()}`),
  );
  page.on("requestfailed", (request) =>
    console.log(`[fail] ${request.method()} ${request.url()} ${request.failure()?.errorText}`),
  );
  page.on("console", (msg) => console.log(`[console] ${msg.type()}: ${msg.text()}`));

  await mockApi(page, {}, { authenticated: false });
  await page.goto("/login");
  await expect(page.getByLabel("Email address")).toBeVisible();
  await loginAsAdmin(page, ADMIN_EMAIL, ADMIN_PASSWORD);
  await expect(page).not.toHaveURL(/\/login/);
  const storagePath = "./e2e/.auth/admin.json";
  mkdirSync(dirname(storagePath), { recursive: true });
  await page.context().storageState({ path: storagePath });
});
