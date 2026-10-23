"use client"

import {
  BlocksIcon,
  BotIcon,
  BoxIcon,
  BuildingIcon,
  ClipboardListIcon,
  GitBranchIcon,
  GlobeIcon,
  KeyRound,
  LayersIcon,
  ListChecksIcon,
  ListVideoIcon,
  LockIcon,
  LogInIcon,
  LogsIcon,
  type LucideIcon,
  MousePointerClickIcon,
  NetworkIcon,
  Pyramid,
  RadioTowerIcon,
  RefreshCwIcon,
  SendIcon,
  Settings,
  Settings2,
  ShieldCheckIcon,
  Sparkles,
  Table2Icon,
  TerminalIcon,
  UsersIcon,
  VariableIcon,
  VaultIcon,
  WorkflowIcon,
} from "lucide-react"
import { useRouter } from "next/navigation"
import { useCallback, useEffect, useState } from "react"
import { useSettingsModal } from "@/components/settings/settings-modal-context"
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
  CommandShortcut,
} from "@/components/ui/command"
import { useEntitlements } from "@/hooks/use-entitlements"
import { useFormsAccess } from "@/hooks/use-forms-access"
import { useWorkflowManager, useWorkspaceManager } from "@/lib/hooks"
import { getWorkspaceLandingPath } from "@/lib/workspace-navigation"
import { useScopes } from "@/providers/scopes"
import { useOptionalWorkspaceId } from "@/providers/workspace-id"

/** A page the command palette can navigate to. */
type CommandPaletteLink = {
  title: string
  href: string
  icon: LucideIcon
  keywords?: string[]
  visible: boolean
}

/**
 * Global Cmd/Ctrl+K command palette for jumping between pages, workflows,
 * workspaces and organization settings.
 *
 * Handlers that own Cmd/Ctrl+K on a specific surface (for example the case
 * workflow trigger) call `preventDefault()` first, and the palette stays shut.
 */
export function CommandPalette() {
  const [open, setOpen] = useState(false)

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (
        event.defaultPrevented ||
        event.repeat ||
        !(event.metaKey || event.ctrlKey) ||
        event.key.toLowerCase() !== "k"
      ) {
        return
      }
      event.preventDefault()
      setOpen((current) => !current)
    }

    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [])

  return (
    <CommandDialog open={open} onOpenChange={setOpen}>
      {open ? <CommandPaletteContent onClose={() => setOpen(false)} /> : null}
    </CommandDialog>
  )
}

function CommandPaletteContent({ onClose }: { onClose: () => void }) {
  const router = useRouter()
  const workspaceId = useOptionalWorkspaceId()
  const { hasScope, hasAnyScope, isLoading: scopesLoading } = useScopes()
  const { hasEntitlement } = useEntitlements()
  const formsAccess = useFormsAccess()
  const { workspaces } = useWorkspaceManager()
  const { setOpen: setSettingsOpen } = useSettingsModal()

  const navigate = useCallback(
    (href: string) => {
      onClose()
      router.push(href)
    },
    [onClose, router]
  )

  const can = (scope: string) => !scopesLoading && hasScope(scope)
  const basePath = workspaceId ? `/workspaces/${workspaceId}` : null
  const canViewWorkflows = can("workflow:read")

  const workspaceLinks: CommandPaletteLink[] = basePath
    ? [
        {
          title: "Chat",
          href: `${basePath}/chat`,
          icon: SendIcon,
          keywords: ["mission control"],
          visible:
            can("agent:read") &&
            can("agent:execute") &&
            hasEntitlement("workspace_chat"),
        },
        {
          title: "Workflows",
          href: `${basePath}/workflows`,
          icon: WorkflowIcon,
          visible: canViewWorkflows,
        },
        {
          title: "Cases",
          href: `${basePath}/cases`,
          icon: LayersIcon,
          visible: can("case:read"),
        },
        {
          title: "Agents",
          href: `${basePath}/agents`,
          icon: MousePointerClickIcon,
          keywords: ["presets"],
          visible: can("agent:read"),
        },
        {
          title: "Skills",
          href: `${basePath}/skills`,
          icon: Pyramid,
          visible: can("agent:read"),
        },
        {
          title: "Forms",
          href: `${basePath}/forms`,
          icon: ClipboardListIcon,
          visible:
            (formsAccess === "enabled" || formsAccess === "locked") &&
            can("form:read") &&
            !scopesLoading &&
            hasAnyScope(["form:update", "form:submission:read"]),
        },
        {
          title: "Tables",
          href: `${basePath}/tables`,
          icon: Table2Icon,
          visible: can("table:read"),
        },
        {
          title: "Variables",
          href: `${basePath}/variables`,
          icon: VariableIcon,
          visible: can("variable:read"),
        },
        {
          title: "Credentials",
          href: `${basePath}/credentials`,
          icon: KeyRound,
          keywords: ["secrets"],
          visible: can("secret:read"),
        },
        {
          title: "Integrations",
          href: `${basePath}/integrations`,
          icon: BlocksIcon,
          keywords: ["oauth", "providers"],
          visible: can("integration:read"),
        },
        {
          title: "MCP servers",
          href: `${basePath}/mcp-servers`,
          icon: Sparkles,
          visible: can("integration:read"),
        },
        {
          title: "Actions",
          href: `${basePath}/actions`,
          icon: BoxIcon,
          keywords: ["registry"],
          visible: can("org:registry:read"),
        },
        {
          title: "Runs",
          href: `${basePath}/runs`,
          icon: ListVideoIcon,
          keywords: ["executions"],
          visible: canViewWorkflows,
        },
        {
          title: "Inbox",
          href: `${basePath}/inbox`,
          icon: ListChecksIcon,
          keywords: ["approvals"],
          visible: can("inbox:read"),
        },
        {
          title: "Members",
          href: `${basePath}/members`,
          icon: UsersIcon,
          keywords: ["workspace members"],
          visible: can("workspace:member:read"),
        },
        {
          title: "Service accounts",
          href: `${basePath}/service-accounts`,
          icon: BotIcon,
          keywords: ["workspace service accounts"],
          visible:
            can("workspace:service_account:read") &&
            hasEntitlement("service_accounts"),
        },
        {
          title: "Git sync",
          href: `${basePath}/git-sync`,
          icon: GitBranchIcon,
          visible:
            !scopesLoading &&
            hasAnyScope(["workspace_sync:sync", "workspace:update"]),
        },
        {
          title: "Tunnels",
          href: `${basePath}/tunnels`,
          icon: NetworkIcon,
          visible: can("tunnel:read"),
        },
        {
          title: "MCP access",
          href: `${basePath}/mcp`,
          icon: TerminalIcon,
          visible: can("workspace:read"),
        },
      ]
    : []

  const canViewOrgSettings = can("org:settings:read")
  const organizationLinks: CommandPaletteLink[] = [
    {
      title: "Organization members",
      href: "/organization/members",
      icon: UsersIcon,
      visible: can("org:member:read"),
    },
    {
      title: "Organization sessions",
      href: "/organization/sessions",
      icon: LogInIcon,
      visible: can("org:member:read"),
    },
    {
      title: "Organization service accounts",
      href: "/organization/settings/service-accounts",
      icon: KeyRound,
      visible:
        can("org:service_account:read") && hasEntitlement("service_accounts"),
    },
    {
      title: "SAML (SSO)",
      href: "/organization/settings/sso",
      icon: LockIcon,
      keywords: ["single sign-on"],
      visible: canViewOrgSettings,
    },
    {
      title: "Domains",
      href: "/organization/settings/domains",
      icon: GlobeIcon,
      visible: canViewOrgSettings,
    },
    {
      title: "SCIM",
      href: "/organization/settings/scim",
      icon: RefreshCwIcon,
      keywords: ["provisioning"],
      visible: can("org:scim:manage"),
    },
    {
      title: "Secret stores",
      href: "/organization/settings/secret-stores",
      icon: VaultIcon,
      visible: can("org:secret:read"),
    },
    {
      title: "Application settings",
      href: "/organization/settings/app",
      icon: Settings2,
      visible: canViewOrgSettings,
    },
    {
      title: "Audit logs",
      href: "/organization/settings/audit",
      icon: LogsIcon,
      visible: canViewOrgSettings,
    },
    {
      title: "IP allowlist",
      href: "/organization/settings/security",
      icon: ShieldCheckIcon,
      keywords: ["security"],
      visible: canViewOrgSettings,
    },
    {
      title: "Git providers",
      href: "/organization/vcs",
      icon: GitBranchIcon,
      keywords: ["vcs", "github"],
      visible: canViewOrgSettings,
    },
    {
      title: "Custom registry",
      href: "/organization/settings/custom-registry",
      icon: BoxIcon,
      visible: canViewOrgSettings,
    },
    {
      title: "Agent configuration",
      href: "/organization/settings/agent",
      icon: MousePointerClickIcon,
      keywords: ["models", "providers"],
      visible: canViewOrgSettings,
    },
    {
      title: "Agent telemetry",
      href: "/organization/settings/agent/telemetry",
      icon: RadioTowerIcon,
      visible: canViewOrgSettings,
    },
  ]

  const visibleWorkspaceLinks = workspaceLinks.filter((link) => link.visible)
  const visibleOrganizationLinks = organizationLinks.filter(
    (link) => link.visible
  )

  return (
    <>
      <CommandInput placeholder="Search pages, workflows and workspaces..." />
      <CommandList className="max-h-[400px]">
        <CommandEmpty>No results found.</CommandEmpty>
        {visibleWorkspaceLinks.length > 0 ? (
          <CommandGroup heading="Go to">
            {visibleWorkspaceLinks.map((link) => (
              <CommandPaletteLinkItem
                key={link.href}
                link={link}
                onSelect={navigate}
              />
            ))}
          </CommandGroup>
        ) : null}
        {workspaceId && canViewWorkflows ? (
          <CommandPaletteWorkflows
            workspaceId={workspaceId}
            onSelect={navigate}
          />
        ) : null}
        {workspaces && workspaces.length > 0 ? (
          <>
            <CommandSeparator />
            <CommandGroup heading="Workspaces">
              {workspaces.map((workspace) => (
                <CommandItem
                  key={workspace.id}
                  value={`${workspace.name} ${workspace.id}`}
                  onSelect={() =>
                    navigate(getWorkspaceLandingPath(workspace.id))
                  }
                >
                  <BuildingIcon />
                  <span className="truncate">{workspace.name}</span>
                  {workspace.id === workspaceId ? (
                    <CommandShortcut className="tracking-normal">
                      Current
                    </CommandShortcut>
                  ) : null}
                </CommandItem>
              ))}
            </CommandGroup>
          </>
        ) : null}
        {visibleOrganizationLinks.length > 0 ? (
          <>
            <CommandSeparator />
            <CommandGroup heading="Organization">
              {visibleOrganizationLinks.map((link) => (
                <CommandPaletteLinkItem
                  key={link.href}
                  link={link}
                  onSelect={navigate}
                />
              ))}
            </CommandGroup>
          </>
        ) : null}
        <CommandSeparator />
        <CommandGroup heading="General">
          <CommandItem
            value="Open settings"
            keywords={["profile", "appearance", "theme", "preferences"]}
            onSelect={() => {
              onClose()
              setSettingsOpen(true)
            }}
          >
            <Settings />
            <span>Open settings</span>
          </CommandItem>
        </CommandGroup>
      </CommandList>
    </>
  )
}

function CommandPaletteLinkItem({
  link,
  onSelect,
}: {
  link: CommandPaletteLink
  onSelect: (href: string) => void
}) {
  const Icon = link.icon
  return (
    <CommandItem
      value={link.title}
      keywords={link.keywords}
      onSelect={() => onSelect(link.href)}
    >
      <Icon />
      <span>{link.title}</span>
    </CommandItem>
  )
}

function CommandPaletteWorkflows({
  workspaceId,
  onSelect,
}: {
  workspaceId: string
  onSelect: (href: string) => void
}) {
  const { workflows } = useWorkflowManager()

  if (!workflows || workflows.length === 0) {
    return null
  }

  return (
    <>
      <CommandSeparator />
      <CommandGroup heading="Workflows">
        {workflows.map((workflow, index) => (
          <CommandItem
            key={workflow.id}
            // Titles can repeat; the index keeps each cmdk value unique
            // without letting base62 IDs match unrelated searches.
            value={`${workflow.title} ${index}`}
            keywords={workflow.alias ? [workflow.alias] : undefined}
            onSelect={() =>
              onSelect(`/workspaces/${workspaceId}/workflows/${workflow.id}`)
            }
          >
            <WorkflowIcon />
            <span className="truncate">{workflow.title}</span>
            {workflow.alias ? (
              <CommandShortcut className="truncate tracking-normal">
                {workflow.alias}
              </CommandShortcut>
            ) : null}
          </CommandItem>
        ))}
      </CommandGroup>
    </>
  )
}
