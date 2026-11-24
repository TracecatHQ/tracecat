"use client"

import { Plus } from "lucide-react"
import type { ReactNode } from "react"
import { getIcon, getMcpProviderIconId, ProviderIcon } from "@/components/icons"
import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type { ToolEntry } from "@/lib/agent-preset-tools"
import { cn } from "@/lib/utils"

/** Focus ring shared by rail controls. */
export const RAIL_FOCUS =
  "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"

/** A clickable 34px list row in the configuration rail. */
export const RAIL_LIST_ROW = cn(
  "group flex min-h-[34px] w-full min-w-0 items-center gap-2.5 rounded-md px-2 py-1 text-left text-[13px] transition-colors hover:bg-foreground/[0.04]",
  RAIL_FOCUS
)

/** A remove button revealed on row hover or focus. */
export const RAIL_REMOVE_BUTTON = cn(
  "size-6 shrink-0 text-muted-foreground opacity-0 hover:text-destructive group-hover:opacity-100 group-focus-within:opacity-100 focus-visible:opacity-100",
  RAIL_FOCUS
)

/** Amber dot and text marking tools that need approval. */
export const APPROVAL_DOT = "size-1.5 shrink-0 rounded-full bg-[#d6a14a]"
export const APPROVAL_TEXT = "text-[#8a5a12] dark:text-[#b58a45]"

/** Rail surface: one step recessed from the page in both themes. */
export const RAIL_SURFACE = "bg-[hsl(0_0%_98%)] dark:bg-[hsl(0_0%_5%)]"

/**
 * A 20px logo tile. `tint` gives agent-owned objects (subagents, skills) the
 * indigo wash; everything else sits on a neutral tile.
 */
export function LogoTile({
  children,
  tint = "neutral",
  className,
}: {
  children: ReactNode
  tint?: "neutral" | "indigo"
  className?: string
}) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "flex size-5 shrink-0 items-center justify-center rounded-[5px] border [&>svg]:size-3 [&>*]:shrink-0",
        tint === "indigo"
          ? "border-transparent bg-[rgba(113,130,219,0.14)] text-[#6670cc] dark:text-[#8b93e6]"
          : "border-zinc-200 bg-zinc-100 text-zinc-600 dark:border-[#2a2a2a] dark:bg-[#1b1b1b] dark:text-zinc-300",
        className
      )}
    >
      {children}
    </span>
  )
}

/** Rail section header: muted label, count, optional extra, and an add button. */
export function RailSectionHeader({
  title,
  count,
  extra,
  addLabel,
  onAdd,
  addDisabled,
  addDisabledReason,
}: {
  title: string
  count?: number
  extra?: ReactNode
  addLabel: string
  onAdd: () => void
  addDisabled?: boolean
  addDisabledReason?: string | null
}) {
  const button = (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      className={cn("size-[26px] shrink-0 text-muted-foreground", RAIL_FOCUS)}
      aria-label={addLabel}
      onClick={onAdd}
      disabled={addDisabled}
    >
      <Plus className="size-3" />
    </Button>
  )
  return (
    <div className="flex items-center pb-1 pl-2">
      <h3 className="text-[12.5px] font-normal text-muted-foreground">
        {title}
        {count ? (
          <span className="ml-1 text-muted-foreground/70">{count}</span>
        ) : null}
      </h3>
      {extra}
      <span className="flex-1" />
      {addDisabledReason ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <span className={cn("inline-flex", RAIL_FOCUS)} tabIndex={0}>
              {button}
            </span>
          </TooltipTrigger>
          <TooltipContent>{addDisabledReason}</TooltipContent>
        </Tooltip>
      ) : (
        button
      )}
    </div>
  )
}

/** Muted "None" line under an empty rail section. */
export function RailEmpty({ children = "None" }: { children?: ReactNode }) {
  return <p className="px-2 text-[13px] text-muted-foreground/70">{children}</p>
}

export function ToolLogo({ entry }: { entry: ToolEntry }) {
  if (entry.integration) {
    return (
      <LogoTile>
        <ProviderIcon
          inline
          providerId={getMcpProviderIconId(entry.integration.slug)}
          className="size-3 rounded-none bg-transparent p-0"
        />
      </LogoTile>
    )
  }
  return getIcon(entry.key, {
    className: "size-5 shrink-0 rounded-[5px] border [&_svg]:size-3",
    inline: true,
  })
}

/** Per-tool approval toggle, right-aligned in a fixed-width slot. */
export function ApprovalButton({
  tool,
  ask,
  disabled,
  onToggle,
  className,
}: {
  tool: string
  ask: boolean
  disabled: boolean
  onToggle: () => void
  className?: string
}) {
  return (
    <span className={cn("flex min-w-[104px] shrink-0 justify-end", className)}>
      <button
        type="button"
        aria-pressed={ask}
        aria-label={`Require approval for ${tool}`}
        disabled={disabled}
        onClick={onToggle}
        className={cn(
          "h-[26px] whitespace-nowrap rounded-[5px] px-2 text-xs transition-colors hover:bg-foreground/[0.06]",
          RAIL_FOCUS,
          ask ? APPROVAL_TEXT : "text-muted-foreground"
        )}
      >
        {ask ? "Needs approval" : "Auto"}
      </button>
    </span>
  )
}

/** "Auto" / "Needs approval" for every row of a drill-in. */
export function ApprovalForAll({
  allAsk,
  allAuto,
  disabled,
  onSet,
}: {
  allAsk: boolean
  allAuto: boolean
  disabled: boolean
  onSet: (ask: boolean) => void
}) {
  return (
    <div
      role="group"
      aria-label="Approval for all tools"
      className="flex rounded-md bg-foreground/[0.06] p-0.5"
    >
      {[
        { ask: false, label: "Auto", pressed: allAuto },
        { ask: true, label: "Needs approval", pressed: allAsk },
      ].map((option) => (
        <button
          key={option.label}
          type="button"
          aria-pressed={option.pressed}
          disabled={disabled}
          onClick={() => onSet(option.ask)}
          className={cn(
            "h-[22px] rounded-[5px] px-2 text-xs transition-colors disabled:opacity-50",
            RAIL_FOCUS,
            option.pressed
              ? "bg-background shadow-[0_1px_2px_rgba(0,0,0,0.08)] dark:bg-accent dark:shadow-none"
              : "text-muted-foreground hover:text-foreground",
            option.pressed && option.ask && APPROVAL_TEXT
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}
