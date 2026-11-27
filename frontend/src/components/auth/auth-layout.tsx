"use client"

import Image from "next/image"
import TracecatIcon from "public/icon.png"
import type React from "react"
import { AuthStatus } from "@/components/auth/auth-status"
import { BrandingStyle } from "@/components/branding/branding-style"
import {
  BaseErrorBoundary,
  type ErrorFallbackProps,
} from "@/components/error-boundary"
import PrivacyPolicy from "@/components/privacy-policy"
import { Button } from "@/components/ui/button"
import { usePublicBranding } from "@/hooks/use-branding"
import { TRACECAT_TAGLINE } from "@/lib/branding"
import { cn } from "@/lib/utils"

function AuthFormError(_props: ErrorFallbackProps) {
  return (
    <div className="flex w-full max-w-md flex-col items-center gap-3 text-center">
      <h2 className="text-lg font-semibold">
        Couldn&apos;t load sign-in options
      </h2>
      <p className="text-sm text-muted-foreground">
        The browser can&apos;t reach the Tracecat API. Check the status panel
        and your network or proxy settings.
      </p>
      <Button
        size="sm"
        variant="outline"
        onClick={() => window.location.reload()}
      >
        Try again
      </Button>
    </div>
  )
}

function AuthLogo({
  logo,
  className,
}: {
  logo: string | null | undefined
  className?: string
}) {
  if (logo) {
    return (
      <img
        src={logo}
        alt="Organization logo"
        className={cn("object-contain", className)}
      />
    )
  }
  return <Image src={TracecatIcon} alt="Tracecat" className={className} />
}

/**
 * Two-panel layout for the sign-in and sign-up pages: branding and service
 * status on the left, the form on the right.
 */
export function AuthLayout({
  organizationSlug,
  children,
}: {
  organizationSlug: string | null
  children: React.ReactNode
}) {
  const { branding, brandingIsLoading } = usePublicBranding(organizationSlug)
  const logo = branding?.logo
  const showTerms = branding?.terms_enabled ?? true

  return (
    <div className="grid size-full lg:grid-cols-2">
      <BrandingStyle branding={branding} />
      <aside className="hidden flex-col items-center justify-center gap-12 bg-zinc-950 p-10 text-zinc-100 lg:flex">
        <div
          className={cn(
            "flex flex-col items-center gap-6 text-center transition-opacity",
            brandingIsLoading && "opacity-0"
          )}
        >
          <AuthLogo logo={logo} className="h-24 w-auto max-w-64" />
          {!logo && (
            <div className="space-y-2">
              <h1 className="text-3xl font-semibold tracking-tight">
                Tracecat
              </h1>
              <p className="text-sm text-zinc-400">{TRACECAT_TAGLINE}</p>
            </div>
          )}
        </div>
        <AuthStatus />
      </aside>
      <main className="flex flex-col items-center justify-center gap-6 overflow-auto py-10">
        <AuthLogo logo={logo} className="h-12 w-auto max-w-40 lg:hidden" />
        <BaseErrorBoundary fallback={AuthFormError}>
          {children}
        </BaseErrorBoundary>
        {showTerms && <PrivacyPolicy className="w-full max-w-md" />}
      </main>
    </div>
  )
}
