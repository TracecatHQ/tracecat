import "@/styles/globals.css"

import type { Metadata } from "next"
import dynamic from "next/dynamic"
import { PublicEnvScript } from "next-runtime-env"
import React from "react"
import { Toaster } from "@/components/ui/toaster"
import { siteConfig } from "@/config/site"
import { fontSans } from "@/lib/fonts"
import { cn } from "@/lib/utils"
import { AuthProvider } from "@/providers/auth"
import type { PHProviderType } from "@/providers/posthog"
import { DefaultQueryClientProvider } from "@/providers/query"

let PostHogPageView: React.ComponentType | undefined = undefined
let PHProvider: PHProviderType | undefined = undefined

if (process.env.NEXT_PUBLIC_APP_ENV === "production") {
  PostHogPageView = dynamic(
    () => import("@/components/analytics/PostHogPageView")
  )
  // Remark: Is there a more elegant way to do this?
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  PHProvider = require("@/providers/posthog").PHProvider
  console.log("PostHog initialized for production environment.")
}
export const metadata: Metadata = {
  title: siteConfig.name,
  description: siteConfig.description,
  icons: {
    icon: "/favicon.png",
    shortcut: "/favicon.png",
    apple: "/apple-touch-icon.png",
  },
}

interface RootLayoutProps {
  children: React.ReactNode
}

export default async function RootLayout({ children }: RootLayoutProps) {
  const MaybeAnalytics = PHProvider ? PHProvider : React.Fragment

  return (
    <html lang="en" className="h-full min-h-screen" suppressHydrationWarning>
      <head>
        <PublicEnvScript />
      </head>
      <MaybeAnalytics>
        <body
          className={cn(
            "h-screen min-h-screen overflow-hidden bg-background font-sans antialiased",
            fontSans.variable
          )}
        >
          <DefaultQueryClientProvider>
            <AuthProvider>
              {PostHogPageView && <PostHogPageView />}
              {children}
            </AuthProvider>
          </DefaultQueryClientProvider>
          <Toaster />
        </body>
      </MaybeAnalytics>
    </html>
  )
}
