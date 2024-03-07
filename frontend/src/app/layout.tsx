import "@/styles/globals.css"

import { Metadata } from "next"

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

export default function RootLayout({ children }: RootLayoutProps) {
  return (
    <>
      <html lang="en" className="h-full" suppressHydrationWarning>
        <head />
        <body
          className={cn(
            "h-full min-h-screen bg-background font-sans antialiased",
            fontSans.className
          )}
        >
          {children}
          <Toaster />
        </body>
      </html>
    </>
  )
}
