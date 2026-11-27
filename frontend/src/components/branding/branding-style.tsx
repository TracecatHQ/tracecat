"use client"

import type { BrandingRead } from "@/client"
import { useOrganizationBranding } from "@/hooks/use-branding"
import { buildBrandingCss } from "@/lib/branding"

/** Applies an organization's primary colors to the theme variables. */
export function BrandingStyle({ branding }: { branding: BrandingRead | null }) {
  const css = buildBrandingCss(branding)
  if (!css) {
    return null
  }
  // Colors are validated as #RRGGBB before they reach the stylesheet.
  return <style data-tracecat-branding>{css}</style>
}

/** Applies the signed-in user's organization branding. */
export function OrganizationBrandingStyle() {
  const { branding } = useOrganizationBranding()
  return <BrandingStyle branding={branding} />
}
