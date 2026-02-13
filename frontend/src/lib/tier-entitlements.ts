import { z } from "zod"
import { $EntitlementsDict, type EntitlementsDict } from "@/client"

type TierEntitlementKey = keyof EntitlementsDict

type TierEntitlementsFormValue = Record<TierEntitlementKey, boolean>

type TierEntitlementDefinition = {
  key: TierEntitlementKey
  label: string
  description?: string
}

const ENTITLEMENT_PROPERTIES = $EntitlementsDict.properties

export const TIER_ENTITLEMENT_KEYS = Object.keys(
  ENTITLEMENT_PROPERTIES
) as TierEntitlementKey[]

export const tierEntitlementsSchema = z.object(
  Object.fromEntries(TIER_ENTITLEMENT_KEYS.map((key) => [key, z.boolean()]))
)

export const TIER_ENTITLEMENTS: TierEntitlementDefinition[] =
  TIER_ENTITLEMENT_KEYS.map((key) => ({
    key,
    label: ENTITLEMENT_PROPERTIES[key].title,
    description: ENTITLEMENT_PROPERTIES[key].description,
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
