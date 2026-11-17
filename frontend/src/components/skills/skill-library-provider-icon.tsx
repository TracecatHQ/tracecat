import { LibraryIcon, UsersIcon } from "lucide-react"
import type { ComponentType } from "react"
import { Icons } from "@/components/icons"
import { mcpCatalogProviderIcons } from "@/components/mcp-catalog-icons"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { libraryMaintainer } from "@/lib/skill-library"
import { cn } from "@/lib/utils"

const PROVIDER_ICONS: Record<
  string,
  ComponentType<{ className?: string }> | undefined
> = {
  aws: mcpCatalogProviderIcons.aws_mcp,
  cloudflare: mcpCatalogProviderIcons.cloudflare_mcp,
  datadog: mcpCatalogProviderIcons.datadog_mcp,
  elastic: mcpCatalogProviderIcons.elastic_mcp,
  feedly: mcpCatalogProviderIcons.feedly_mcp,
  "google-secops": mcpCatalogProviderIcons.google_cloud_secops_mcp,
  splunk: mcpCatalogProviderIcons.splunk_mcp,
  tracecat: Icons.logo,
}

/**
 * Render a library provider's logo in a bordered tile.
 *
 * @param props Provider slug and tile size classes.
 * @returns The provider logo, or a library glyph when none is known.
 */
export function SkillLibraryProviderIcon({
  providerSlug,
  className,
}: {
  providerSlug: string
  className?: string
}) {
  const Icon = PROVIDER_ICONS[providerSlug]
  return (
    <div
      className={cn(
        "flex size-8 shrink-0 items-center justify-center rounded-md border bg-background p-1.5",
        className
      )}
    >
      {Icon ? (
        <Icon className="size-full" />
      ) : (
        <LibraryIcon className="size-full text-muted-foreground" />
      )}
    </div>
  )
}

/**
 * Corner stamp on a skill tile when someone other than its group maintains it.
 *
 * @param props The skill source's maintainer; null renders nothing.
 * @returns A tooltip-labelled stamp, or null for official skills.
 */
export function SkillLibraryMaintainerMark({
  provider,
}: {
  provider: string | null | undefined
}) {
  const maintainer = libraryMaintainer(provider)
  if (maintainer === "official" || !provider) return null
  const label = `Maintained by ${provider}`
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          aria-label={label}
          className="absolute -bottom-1.5 -right-1.5 flex size-4 cursor-help items-center justify-center rounded-[4px] ring-[1.5px] ring-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {maintainer === "tracecat" ? (
            <span className="flex size-full items-center justify-center rounded-[4px] bg-foreground p-[2px] text-background">
              <Icons.logo className="size-full" />
            </span>
          ) : (
            <span className="flex size-full items-center justify-center rounded-full bg-muted text-muted-foreground">
              <UsersIcon className="size-2.5" />
            </span>
          )}
        </button>
      </TooltipTrigger>
      <TooltipContent side="bottom" align="start">
        <p className="font-medium">{label}</p>
        {maintainer === "tracecat" ? (
          <p className="opacity-80">
            Ships with Tracecat and updates with each release.
          </p>
        ) : null}
      </TooltipContent>
    </Tooltip>
  )
}
