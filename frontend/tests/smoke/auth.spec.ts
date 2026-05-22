import { expect, test } from "@playwright/test"

import {
  createOrganizationInvitation,
  DEFAULT_SMOKE_PASSWORD,
  expectWorkspaceLanding,
  freshEmail,
  SUPERUSER_EMAIL,
  SUPERUSER_PASSWORD,
  signInWithBasicAuth,
  TENANT_USER_EMAIL,
} from "./utils/auth"

test.use({ storageState: { cookies: [], origins: [] } })

test.describe("authentication", () => {
  test("anonymous visitor can start basic auth", async ({ page }) => {
    await page.goto("/")
    await page.getByRole("link", { name: /Sign in/i }).click()

    await expect(page.getByLabel("Email")).toBeVisible()
    await page.getByLabel("Email").fill(TENANT_USER_EMAIL)
    await page.getByRole("button", { name: "Continue" }).click()

    await expect(page.getByLabel("Password")).toBeVisible()
  })

  test("fresh invited user can sign up and land in a workspace", async ({
    page,
    request,
  }, testInfo) => {
    const email = freshEmail(testInfo.title)
    const token = await createOrganizationInvitation(request, email)
    const returnUrl = `/invitations/accept?token=${token}`

    await page.goto(`/sign-up?returnUrl=${encodeURIComponent(returnUrl)}`)
    await expect(
      page.getByRole("heading", { name: "Create an account" })
    ).toBeVisible()

    await page.getByLabel("Email").fill(email)
    await page.getByLabel("Password").fill(DEFAULT_SMOKE_PASSWORD)
    await page.getByRole("button", { name: "Create account" }).click()

    await expectWorkspaceLanding(page)
  })

  test("seeded superuser can access the admin console", async ({ page }) => {
    await signInWithBasicAuth(page, SUPERUSER_EMAIL, SUPERUSER_PASSWORD)

    await page.goto("/admin/users")
    await expect(page.getByRole("heading", { name: "Users" })).toBeVisible()
    await expect(
      page.getByText("Manage users and superuser access across the platform.")
    ).toBeVisible()
  })
})
