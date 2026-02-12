import { z } from "zod"
import { $EntitlementsDict, type EntitlementsDict } from "@/client"

type TierEntitlementKey = keyof EntitlementsDict

type TierEntitlementsFormValue = Record<TierEntitlementKey, boolean>

type TierEntitlementDefinition = {
  key: TierEntitlementKey
  label: string
  description: string
}

const ENTITLEMENT_PROPERTIES = $EntitlementsDict.properties as Record<
  TierEntitlementKey,
  {
    title?: string
  }
>

export const TIER_ENTITLEMENT_KEYS = Object.keys(
  ENTITLEMENT_PROPERTIES
) as TierEntitlementKey[]

const ENTITLEMENT_DESCRIPTIONS: Partial<Record<TierEntitlementKey, string>> = {
  custom_registry: "Allow using non-default registry repositories.",
  sso: "Allow SSO configuration for the organization.",
  git_sync: "Allow syncing workflows with external git repositories.",
}

function titleCaseFromSnakeCase(value: string): string {
  return value
    .split("_")
    .map((word) => word.slice(0, 1).toUpperCase() + word.slice(1))
    .join(" ")
}

export const tierEntitlementsSchema = z.object(
  Object.fromEntries(
    TIER_ENTITLEMENT_KEYS.map((key) => [key, z.boolean()])
  ) as Record<TierEntitlementKey, z.ZodBoolean>
)

export const TIER_ENTITLEMENTS: TierEntitlementDefinition[] =
  TIER_ENTITLEMENT_KEYS.map((key) => ({
    key,
    label:
      ENTITLEMENT_PROPERTIES[key]?.title ?? titleCaseFromSnakeCase(String(key)),
    description:
      ENTITLEMENT_DESCRIPTIONS[key] ??
      "Enable this feature for organizations on this tier.",
  }))

export function withDefaultTierEntitlements(
  entitlements?: EntitlementsDict | null
): TierEntitlementsFormValue {
  const resolved = {} as TierEntitlementsFormValue
  for (const key of TIER_ENTITLEMENT_KEYS) {
    resolved[key] = entitlements?.[key] ?? false
  }
  return resolved
}
