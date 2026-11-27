"use client"

import Image from "next/image"
import TracecatIcon from "public/icon.png"
import PrivacyPolicy from "@/components/privacy-policy"
import { usePublicBranding } from "@/hooks/use-branding"

/** Organization logo for the auth pages, falling back to the Tracecat icon. */
export function AuthLogo({
  organizationSlug,
}: {
  organizationSlug?: string | null
}) {
  const { branding, brandingIsLoading } = usePublicBranding(
    organizationSlug ?? null
  )
  if (brandingIsLoading) {
    return <div className="mb-8 size-16" />
  }
  if (branding?.logo) {
    return (
      <img
        src={branding.logo}
        alt="Organization logo"
        className="mb-8 h-16 w-auto max-w-48 object-contain"
      />
    )
  }
  return <Image src={TracecatIcon} alt="Tracecat" className="mb-8 size-16" />
}

/** Terms of Service and Privacy Policy notice, unless the org disabled it. */
export function AuthTerms({
  organizationSlug,
}: {
  organizationSlug?: string | null
}) {
  const { branding, brandingIsLoading } = usePublicBranding(
    organizationSlug ?? null
  )
  if (brandingIsLoading || branding?.terms_enabled === false) {
    return null
  }
  return <PrivacyPolicy className="pt-4" />
}
