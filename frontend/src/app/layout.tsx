import "@/styles/globals.css"

import { Metadata } from "next"
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

export default async function RootLayout({ children }: RootLayoutProps) {
  const supabase = createClient()

  const {
    data: { session },
  } = await supabase.auth.getSession()
  return (
    <>
      <html lang="en" className="h-full min-h-screen" suppressHydrationWarning>
        <head />
        <body
          className={cn(
            "h-screen min-h-screen overflow-hidden bg-background font-sans antialiased",
            fontSans.className
          )}
        >
          <SessionContextProvider initialSession={session}>
            {children}
          </SessionContextProvider>
          <Toaster />
        </body>
      </html>
    </>
  )
}
