import "@/styles/globals.css"

import React from "react"
import { Metadata } from "next"
import dynamic from "next/dynamic"
import { DefaultQueryClientProvider } from "@/providers/query"
import { SessionContextProvider } from "@/providers/session"
import { createClient } from "@/utils/supabase/server"

import { siteConfig } from "@/config/site"
import { fontSans } from "@/lib/fonts"
import { cn } from "@/lib/utils"
import { Toaster } from "@/components/ui/toaster"

export const metadata: Metadata = {
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

let PostHogPageView: any
let PHProvider: any

if (process.env.NEXT_PUBLIC_APP_ENV === "production") {
  PostHogPageView = dynamic(
    () => import("@/components/analytics/PostHogPageView"),
    {
      ssr: false,
    }
  )
  PHProvider = require("@/providers/posthog").PHProvider
  console.log("PostHog initialized for production environment.")
}

export default async function RootLayout({ children }: RootLayoutProps) {
  const supabase = createClient()

  const {
    data: { session },
  } = await supabase.auth.getSession()

  const MaybeAnalytics = PHProvider ? PHProvider : React.Fragment

  return (
    <>
      <html lang="en" className="h-full min-h-screen" suppressHydrationWarning>
        <head />
        <MaybeAnalytics>
          <body
            className={cn(
              "h-screen min-h-screen overflow-hidden bg-background font-sans antialiased",
              fontSans.variable
            )}
          >
            <SessionContextProvider initialSession={session}>
              <DefaultQueryClientProvider>
                {PostHogPageView && <PostHogPageView />}
                {children}
              </DefaultQueryClientProvider>
            </SessionContextProvider>
            <Toaster />
          </body>
        </MaybeAnalytics>
      </html>
    </>
  )
}
