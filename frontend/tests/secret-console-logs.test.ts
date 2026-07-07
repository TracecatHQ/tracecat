import { readFileSync } from "node:fs"
import { join } from "node:path"

const SECRET_MANAGEMENT_COMPONENTS = [
  "src/components/organization/org-secret-create.tsx",
  "src/components/organization/org-secret-update.tsx",
  "src/components/organization/org-secret-delete.tsx",
  "src/components/organization/org-secrets-table.tsx",
  "src/components/workspaces/create-credential-dialog.tsx",
  "src/components/workspaces/edit-workspace-secret.tsx",
  "src/components/workspaces/delete-workspace-secret.tsx",
  "src/components/workspaces/workspace-credentials-inventory.tsx",
]

const SECRET_HOOKS = ["useWorkspaceSecrets", "useOrgSecrets"]

const SECRET_ERROR_MESSAGE_COMPONENTS = [
  "src/components/organization/org-secrets-table.tsx",
  "src/components/workspaces/create-credential-dialog.tsx",
  "src/components/workspaces/workspace-credentials-inventory.tsx",
]

const SECRET_DELETE_COMPONENTS = [
  "src/components/organization/org-secret-delete.tsx",
  "src/components/workspaces/delete-workspace-secret.tsx",
]

const disallowedConsolePattern =
  /\b(?:console|window\.console|globalThis\.console)\s*\.\s*(?:log|debug|info|warn|error)\s*\(/

const disallowedRawErrorMessagePattern = /\w+Error\?\.message/

function readFrontendSource(relativePath: string) {
  return readFileSync(join(process.cwd(), relativePath), "utf8")
}

function extractHookSource(source: string, hookName: string) {
  const start = source.indexOf(`export function ${hookName}(`)
  expect(start).toBeGreaterThanOrEqual(0)

  const nextExport = source.indexOf("\nexport function ", start + 1)
  return nextExport === -1
    ? source.slice(start)
    : source.slice(start, nextExport)
}

describe("secret management logging", () => {
  it.each(SECRET_MANAGEMENT_COMPONENTS)(
    "does not write secret data to the browser console from %s",
    (relativePath) => {
      const source = readFrontendSource(relativePath)

      expect(source).not.toMatch(disallowedConsolePattern)
    }
  )

  it.each(SECRET_HOOKS)(
    "does not log secret mutation errors from %s",
    (hookName) => {
      const source = readFrontendSource("src/lib/hooks.tsx")
      const hookSource = extractHookSource(source, hookName)

      expect(hookSource).not.toMatch(disallowedConsolePattern)
    }
  )

  it.each(SECRET_ERROR_MESSAGE_COMPONENTS)(
    "does not render raw API error messages from %s",
    (relativePath) => {
      const source = readFrontendSource(relativePath)

      expect(source).not.toMatch(disallowedRawErrorMessagePattern)
    }
  )

  it.each(SECRET_DELETE_COMPONENTS)(
    "catches rejected secret deletion promises in %s",
    (relativePath) => {
      const source = readFrontendSource(relativePath)

      expect(source).toContain("await deleteSecretById(selectedSecret)")
      expect(source).toContain("} catch {")
    }
  )
})
