"use client"

import { BookText, Settings, ShieldCheckIcon } from "lucide-react"
import Link from "next/link"
import { useSettingsModal } from "@/components/settings/settings-modal-context"
import { Separator } from "@/components/ui/separator"
import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"
import { siteConfig } from "@/config/site"
import { useAuth } from "@/hooks/use-auth"

export function SidebarUserNav() {
  const { user } = useAuth()
  const { setOpen } = useSettingsModal()

  return (
    <SidebarMenu>
      <Separator className="my-1" />

      <SidebarMenuItem>
        <SidebarMenuButton onClick={() => setOpen(true)} tooltip="Settings">
          <Settings className="size-4" />
          <span>Settings</span>
        </SidebarMenuButton>
      </SidebarMenuItem>

      {user?.isSuperuser && (
        <SidebarMenuItem>
          <SidebarMenuButton asChild tooltip="Admin">
            <Link href="/admin">
              <ShieldCheckIcon className="size-4" />
              <span>Admin</span>
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
      )}

      <SidebarMenuItem>
        <SidebarMenuButton asChild tooltip="Docs">
          <Link
            href={siteConfig.links.docs}
            target="_blank"
            rel="noopener noreferrer"
          >
            <BookText className="size-4" />
            <span>Docs</span>
          </Link>
        </SidebarMenuButton>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}
