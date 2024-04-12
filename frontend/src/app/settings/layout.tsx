import { Metadata } from "next"
import { createClient } from "@/utils/supabase/server"

import { Separator } from "@/components/ui/separator"
import Navbar from "@/components/nav/navbar"

import { SidebarNav } from "./sidebar-nav"

export const metadata: Metadata = {
  title: "Forms",
  description: "Advanced form example using react-hook-form and Zod.",
}

const sidebarNavItems = [
  {
    title: "Credentials",
    href: "/settings/credentials",
  },
]

interface SettingsLayoutProps {
  children: React.ReactNode
}

export default async function SettingsLayout({
  children,
}: SettingsLayoutProps) {
  const supabase = createClient()
  const {
    data: { session },
  } = await supabase.auth.getSession()
  return (
    <div className="no-scrollbar h-screen max-h-screen overflow-auto">
      <Navbar session={session} />
      <div className="container space-y-6 p-10 pb-16 pt-16 md:block">
        <div className="space-y-0.5">
          <h2 className="text-2xl font-bold tracking-tight">Settings</h2>
          <p className="text-muted-foreground">
            Manage your account settings and credentials.
          </p>
        </div>
        <Separator className="my-6" />
        <div className="flex flex-col space-y-8 lg:flex-row lg:space-x-12 lg:space-y-0">
          <aside className="-mx-4 lg:w-1/5">
            <SidebarNav items={sidebarNavItems} />
          </aside>
          <div className="w-full flex-1">{children}</div>
        </div>
      </div>
    </div>
  )
}
