import { expect, test } from "@playwright/test"

import { expectWorkspaceLanding } from "./utils/auth"

test.describe("workspace first run", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/workspaces")
    await expectWorkspaceLanding(page)
  })

  test("core OSS workspace navigation is visible", async ({ page }) => {
    for (const item of [
      "Workflows",
      "Cases",
      "Variables",
      "Credentials",
      "Integrations",
      "Actions",
    ]) {
      await expect(
        page.locator('[data-sidebar="menu-button"]').filter({ hasText: item })
      ).toBeVisible()
    }
  })

  test("user can create a workflow and load the builder", async ({ page }) => {
    await page.getByRole("link", { name: "Workflows" }).click()
    await page.waitForURL(/\/workspaces\/[^/]+\/workflows(\/|$|\?)/)

    await page.getByRole("button", { name: /Create new/i }).click()
    await page
      .getByRole("menuitem")
      .filter({ hasText: "Start from scratch" })
      .click()

    await page.waitForURL(/\/workspaces\/[^/]+\/workflows\/[^/]+$/)
    await expect(page.getByText("Workflow trigger")).toBeVisible()
    await expect(page.getByRole("button", { name: "Publish" })).toBeVisible()
  })
})
