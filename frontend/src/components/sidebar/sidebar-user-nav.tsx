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
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { siteConfig } from "@/config/site"
import { useAuth } from "@/hooks/use-auth"
import { useOrganization } from "@/hooks/use-organization"
import { useAppInfo } from "@/lib/hooks"

export function SidebarUserNav() {
  const { user } = useAuth()
  const { setOpen } = useSettingsModal()
  const { organization, isLoading } = useOrganization()
  const { appInfo } = useAppInfo()
  const multiTenantEnabled = appInfo?.ee_multi_tenant ?? true
  const adminUrl = multiTenantEnabled ? "/admin" : "/admin/registry"

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
          <Tooltip>
            <TooltipTrigger asChild>
              <SidebarMenuButton asChild>
                <Link href={adminUrl}>
                  <ShieldCheckIcon className="size-4" />
                  <span>Admin</span>
                </Link>
              </SidebarMenuButton>
            </TooltipTrigger>
            <TooltipContent side="top">
              {isLoading ? "..." : (organization?.name ?? "Organization")}
            </TooltipContent>
          </Tooltip>
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
