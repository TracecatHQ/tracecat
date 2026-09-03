import {
  type APIRequestContext,
  expect,
  type TestInfo,
  test,
} from "@playwright/test"

import { expectWorkspaceLanding, getWorkspaceId } from "./utils/auth"

type Entitlements = {
  git_sync: boolean
  agent_addons: boolean
}

async function getEntitlements(
  pageRequest: APIRequestContext
): Promise<Entitlements> {
  const response = await pageRequest.get("/api/organization/entitlements")
  if (!response.ok()) {
    throw new Error(await response.text())
  }
  return (await response.json()) as Entitlements
}

async function skipIfEntitled(
  pageRequest: APIRequestContext,
  testInfo: TestInfo,
  entitlement: keyof Entitlements
) {
  const entitlements = await getEntitlements(pageRequest)
  if (!entitlements[entitlement]) {
    return
  }
  if (process.env.CI) {
    expect(
      entitlements[entitlement],
      "Run these tests against an OSS entitlement baseline"
    ).toBe(false)
  }
  testInfo.skip(true, `Requires ${entitlement} disabled`)
}

test.describe("entitlement gates", () => {
  test("locked sidebar item opens upgrade modal without navigation", async ({
    page,
  }, testInfo) => {
    await skipIfEntitled(page.request, testInfo, "agent_addons")

    await page.goto("/workspaces")
    await expectWorkspaceLanding(page)

    await expect(async () => {
      await page.getByRole("button", { name: "Skills" }).click({
        timeout: 2_000,
      })
    }).toPass()

    await expect(
      page.getByRole("dialog", { name: "Upgrade to unlock this feature" })
    ).toBeVisible()
    await expect(page).not.toHaveURL(/\/skills(\/|$|\?)/)
  })

  test("direct inbox route renders the entitlement empty state", async ({
    page,
  }, testInfo) => {
    await skipIfEntitled(page.request, testInfo, "agent_addons")

    await page.goto("/workspaces")
    const workspaceId = await getWorkspaceId(page)

    await page.goto(`/workspaces/${workspaceId}/inbox`, {
      waitUntil: "domcontentloaded",
    })

    await expect(page.getByText("Enterprise only")).toBeVisible()
    await expect(
      page.getByText(
        "Advanced AI agents (human-in-the-loop and subagents) are only available on enterprise plans."
      )
    ).toBeVisible()
  })

  test("direct Git sync route renders the entitlement empty state", async ({
    page,
  }, testInfo) => {
    await skipIfEntitled(page.request, testInfo, "git_sync")

    await page.goto("/organization/vcs")

    await expect(page.getByRole("heading", { name: "Git sync" })).toBeVisible()
    await expect(page.getByText("Upgrade required")).toBeVisible()
    await expect(
      page.getByText("Git sync is unavailable on your current plan.")
    ).toBeVisible()
  })
})
