import { type APIRequestContext, expect, type Page } from "@playwright/test"

export const TENANT_USER_EMAIL =
  process.env.SMOKE_TENANT_EMAIL ?? "dev@tracecat.com"
export const TENANT_USER_PASSWORD =
  process.env.SMOKE_TENANT_PASSWORD ?? "password1234"
export const SUPERUSER_EMAIL =
  process.env.SMOKE_SUPERUSER_EMAIL ?? "test@tracecat.com"
export const SUPERUSER_PASSWORD =
  process.env.SMOKE_SUPERUSER_PASSWORD ?? "password1234"
export const DEFAULT_SMOKE_PASSWORD =
  process.env.SMOKE_FRESH_USER_PASSWORD ?? "password1234!"

type RoleRead = {
  id: string
  slug?: string | null
}

type RoleList = {
  items: RoleRead[]
}

type InvitationRead = {
  id: string
}

type InvitationTokenRead = {
  token: string
}

/**
 * Build a unique, deterministic-enough email address for one smoke test run.
 */
export function freshEmail(testTitle: string): string {
  const slug = testTitle
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 32)
  return `smoke-${slug}-${Date.now()}-${Math.random()
    .toString(36)
    .slice(2, 8)}@smoke.tracecat.dev`
}

/**
 * Sign in through the visible basic-auth UI, including auth discovery.
 */
export async function signInWithBasicAuth(
  page: Page,
  email: string,
  password: string
): Promise<void> {
  await page.goto("/sign-in")
  await expect(page.getByLabel("Email")).toBeVisible()

  await page.getByLabel("Email").fill(email)
  await page.getByRole("button", { name: "Continue" }).click()
  await expect(page.getByLabel("Password")).toBeVisible()

  await page.getByLabel("Password").fill(password)
  await page.getByRole("button", { name: "Sign In" }).click()
  await page.waitForURL(/\/workspaces(\/|$)/)
}

/**
 * Authenticate an API request context using the app's basic-auth endpoint.
 */
export async function signInRequest(
  request: APIRequestContext,
  email: string,
  password: string
): Promise<void> {
  const response = await request.post("/api/auth/login", {
    form: {
      username: email,
      password,
    },
  })
  if (!response.ok()) {
    throw new Error(await response.text())
  }
}

/**
 * Create an organization invitation and return its raw invitation token.
 */
export async function createOrganizationInvitation(
  request: APIRequestContext,
  email: string
): Promise<string> {
  await signInRequest(request, TENANT_USER_EMAIL, TENANT_USER_PASSWORD)

  const rolesResponse = await request.get("/api/rbac/roles")
  if (!rolesResponse.ok()) {
    throw new Error(await rolesResponse.text())
  }
  const roles = (await rolesResponse.json()) as RoleList
  const role =
    roles.items.find((item) => item.slug === "organization-admin") ??
    roles.items.find((item) => item.slug === "organization-owner")
  expect(
    role,
    "Expected an organization role for invitation setup"
  ).toBeTruthy()
  if (!role) {
    throw new Error("Expected an organization role for invitation setup")
  }

  const invitationResponse = await request.post(
    "/api/organization/invitations",
    {
      data: {
        email,
        role_id: role.id,
      },
    }
  )
  if (!invitationResponse.ok()) {
    throw new Error(await invitationResponse.text())
  }
  const invitation = (await invitationResponse.json()) as InvitationRead

  const tokenResponse = await request.get(
    `/api/organization/invitations/${invitation.id}/token`
  )
  if (!tokenResponse.ok()) {
    throw new Error(await tokenResponse.text())
  }
  const token = (await tokenResponse.json()) as InvitationTokenRead
  return token.token
}

/**
 * Wait until the workspace redirect settles on Chat.
 */
export async function expectWorkspaceLanding(page: Page): Promise<void> {
  await page.waitForURL(/\/workspaces\/[^/]+\/chat(\/|$|\?)/)
  await expect(page.getByRole("link", { name: "Chat" })).toBeVisible()
}

/**
 * Read the active workspace ID from the settled workspace URL.
 */
export async function getWorkspaceId(page: Page): Promise<string> {
  await expectWorkspaceLanding(page)
  const match = page.url().match(/\/workspaces\/([^/]+)/)
  expect(match?.[1], "Expected workspace ID in URL").toBeTruthy()
  return match?.[1] ?? ""
}
