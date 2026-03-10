import type { SecretDefinition, SecretType } from "@/client"
import type { WorkspaceSecretListItem } from "@/lib/hooks"

export type CredentialConnectionFilter = "all" | "connected" | "not_connected"
export type CredentialSecretTypeFilter = "all" | SecretType

export interface CredentialGroup {
  name: string
  template: SecretDefinition | null
  secrets: WorkspaceSecretListItem[]
  environments: string[]
  secretTypes: SecretType[]
  secretType: SecretType
  isPrebuilt: boolean
  isConnected: boolean
}

export const credentialSecretTypeLabels: Record<SecretType, string> = {
  custom: "Custom",
  "ssh-key": "SSH key",
  mtls: "mTLS",
  "ca-cert": "CA certificate",
  "github-app": "GitHub app",
}

export function normalizeSecretEnvironment(
  environment: string | null | undefined
) {
  return environment?.trim() || "default"
}

function getManualSecretType(secrets: WorkspaceSecretListItem[]): SecretType {
  if (secrets.length === 0) {
    return "custom"
  }
  const [firstSecret, ...rest] = secrets
  return rest.every((secret) => secret.type === firstSecret.type)
    ? firstSecret.type
    : "custom"
}

function getSecretTypes(secrets: WorkspaceSecretListItem[]): SecretType[] {
  return Array.from(new Set(secrets.map((secret) => secret.type))).sort(
    (a, b) => a.localeCompare(b)
  )
}

export function getCredentialSecretTypeSummary(group: CredentialGroup): string {
  const labels =
    group.secretTypes.length > 0
      ? group.secretTypes.map((type) => credentialSecretTypeLabels[type])
      : [credentialSecretTypeLabels[group.secretType]]

  return labels.join(", ")
}

export function buildCredentialGroups(
  secretDefinitions: SecretDefinition[],
  secrets: WorkspaceSecretListItem[]
): CredentialGroup[] {
  const definitionByName = new Map(
    secretDefinitions.map((definition) => [definition.name, definition])
  )
  const secretsByName = new Map<string, WorkspaceSecretListItem[]>()

  for (const secret of secrets) {
    const currentSecrets = secretsByName.get(secret.name) ?? []
    currentSecrets.push(secret)
    secretsByName.set(secret.name, currentSecrets)
  }

  const groups: CredentialGroup[] = secretDefinitions.map((definition) => {
    const configuredSecrets = [
      ...(secretsByName.get(definition.name) ?? []),
    ].sort((a, b) =>
      normalizeSecretEnvironment(a.environment).localeCompare(
        normalizeSecretEnvironment(b.environment)
      )
    )
    const secretTypes: SecretType[] =
      configuredSecrets.length > 0
        ? getSecretTypes(configuredSecrets)
        : ["custom"]

    return {
      name: definition.name,
      template: definition,
      secrets: configuredSecrets,
      environments: configuredSecrets.map((secret) =>
        normalizeSecretEnvironment(secret.environment)
      ),
      secretTypes,
      secretType: getManualSecretType(configuredSecrets),
      isPrebuilt: true,
      isConnected: configuredSecrets.length > 0,
    }
  })

  const manualGroups = Array.from(secretsByName.entries())
    .filter(([name]) => !definitionByName.has(name))
    .map(([name, groupSecrets]) => {
      const secretsForGroup = [...groupSecrets].sort((a, b) =>
        normalizeSecretEnvironment(a.environment).localeCompare(
          normalizeSecretEnvironment(b.environment)
        )
      )

      return {
        name,
        template: null,
        secrets: secretsForGroup,
        environments: secretsForGroup.map((secret) =>
          normalizeSecretEnvironment(secret.environment)
        ),
        secretTypes: getSecretTypes(secretsForGroup),
        secretType: getManualSecretType(secretsForGroup),
        isPrebuilt: false,
        isConnected: true,
      } satisfies CredentialGroup
    })

  return [...groups, ...manualGroups].sort((a, b) => {
    if (a.isConnected !== b.isConnected) {
      return a.isConnected ? -1 : 1
    }
    if (a.isPrebuilt !== b.isPrebuilt) {
      return a.isPrebuilt ? -1 : 1
    }
    return a.name.localeCompare(b.name)
  })
}
