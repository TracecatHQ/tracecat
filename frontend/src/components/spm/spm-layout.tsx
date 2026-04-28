"use client"

import * as AccordionPrimitive from "@radix-ui/react-accordion"
import {
  ChevronRightIcon,
  CircleDotIcon,
  EyeIcon,
  type LucideIcon,
  RefreshCwIcon,
  SearchIcon,
  ShieldCheckIcon,
} from "lucide-react"
import type { ComponentType, ReactNode } from "react"
import { Badge } from "@/components/ui/badge"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { Input } from "@/components/ui/input"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import type { BadgeVariant } from "./spm-common"

export function SpmEmptyState(props: {
  description: string
  title: string
  icon?: ReactNode
}) {
  return (
    <div className="flex h-full min-h-[260px] items-center justify-center">
      <Empty className="gap-4">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            {props.icon ?? <ShieldCheckIcon className="h-6 w-6" />}
          </EmptyMedia>
          <EmptyTitle>{props.title}</EmptyTitle>
          <EmptyDescription>{props.description}</EmptyDescription>
        </EmptyHeader>
      </Empty>
    </div>
  )
}

export function SpmListShell(props: {
  action?: ReactNode
  children: ReactNode
  count: number
  countLabel: string
  filters?: ReactNode
  hasFilters?: boolean
  headerStatus?: ReactNode
  hideToolbarCount?: boolean
  icon: ComponentType<{ className?: string }>
  onSearchChange: (value: string) => void
  resetButton?: ReactNode
  searchPlaceholder: string
  searchQuery: string
  title: string
}) {
  const Icon = props.icon
  return (
    <div className="flex size-full flex-col pt-2">
      <header className="flex h-10 shrink-0 items-center border-b pl-3 pr-4">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-7 w-7 shrink-0 items-center justify-center">
            <Icon className="size-4 text-muted-foreground" />
          </div>
          <span className="truncate text-sm font-medium">{props.title}</span>
        </div>
        <div className="ml-auto flex items-center gap-2">
          {props.headerStatus}
          {props.action}
        </div>
      </header>

      <div className="shrink-0 border-b">
        <div className="flex h-10 items-center border-b pl-3 pr-4">
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex h-7 w-7 shrink-0 items-center justify-center">
              <SearchIcon className="size-4 text-muted-foreground" />
            </div>
            <Input
              type="text"
              placeholder={props.searchPlaceholder}
              value={props.searchQuery}
              onChange={(event) => props.onSearchChange(event.target.value)}
              className={cn(
                "h-7 w-56 border-none bg-transparent p-0 text-sm",
                "shadow-none outline-none placeholder:text-muted-foreground",
                "focus-visible:ring-0 focus-visible:ring-offset-0"
              )}
            />
          </div>
          {props.hideToolbarCount ? null : (
            <div className="ml-auto flex items-center gap-2">
              <span className="text-xs text-muted-foreground">
                {props.count} {props.countLabel}
              </span>
            </div>
          )}
        </div>
        {props.filters ? (
          <div className="flex flex-wrap items-center gap-2 py-2 pl-3 pr-4">
            {props.filters}
            {props.hasFilters ? props.resetButton : null}
          </div>
        ) : null}
      </div>

      <div className="min-h-0 flex-1 overflow-auto">{props.children}</div>
    </div>
  )
}

export function FeedSection(props: { children: ReactNode; title?: string }) {
  return (
    <section className="border-b last:border-b-0">
      {props.title ? (
        <div className="flex h-9 items-center border-b px-3 text-xs font-medium text-muted-foreground">
          {props.title}
        </div>
      ) : null}
      {props.children}
    </section>
  )
}

export function SpmAccordion<TValue extends string>(props: {
  children: (value: TValue) => ReactNode
  groups: Array<{
    count: number
    icon: LucideIcon
    iconClassName: string
    label: string
    triggerClassName: string
    value: TValue
  }>
}) {
  const defaultOpen = props.groups
    .filter((group) => group.count > 0)
    .map((group) => group.value)

  return (
    <div className="h-full overflow-auto">
      <AccordionPrimitive.Root
        type="multiple"
        defaultValue={defaultOpen}
        className="w-full"
      >
        {props.groups.map((group) => {
          const GroupIcon = group.icon
          return (
            <AccordionPrimitive.Item
              key={group.value}
              value={group.value}
              className="group/accordion border-b border-border/50"
            >
              <AccordionPrimitive.Header className="flex">
                <AccordionPrimitive.Trigger
                  className={cn(
                    "flex w-full items-center gap-1 border-l-2 border-l-transparent py-1.5 pl-[10px] pr-3 text-left transition-colors",
                    "hover:bg-muted/50",
                    "[&[data-state=open]_.chevron]:rotate-90",
                    "data-[state=open]:border-l-current",
                    group.triggerClassName
                  )}
                >
                  <div className="flex h-7 w-7 shrink-0 items-center justify-center">
                    <ChevronRightIcon className="chevron size-4 text-muted-foreground transition-transform duration-200" />
                  </div>
                  <div className="flex items-center gap-1.5">
                    <GroupIcon
                      className={cn("size-4 shrink-0", group.iconClassName)}
                    />
                    <span className="text-xs font-medium">{group.label}</span>
                    <span className="text-xs text-muted-foreground">
                      {group.count}
                    </span>
                  </div>
                </AccordionPrimitive.Trigger>
              </AccordionPrimitive.Header>
              <AccordionPrimitive.Content className="overflow-hidden data-[state=closed]:animate-accordion-up data-[state=open]:animate-accordion-down">
                <div className="ml-[18px] divide-y divide-border/50">
                  {props.children(group.value)}
                </div>
              </AccordionPrimitive.Content>
            </AccordionPrimitive.Item>
          )
        })}
      </AccordionPrimitive.Root>
    </div>
  )
}

export function SpmCompactRow(props: {
  actions?: ReactNode
  badges?: ReactNode
  icon?: ReactNode
  isSelected?: boolean
  meta?: ReactNode
  onClick?: () => void
  subtitle?: ReactNode
  title: ReactNode
}) {
  const content = (
    <>
      <div className="flex h-7 w-7 shrink-0 items-center justify-center">
        {props.icon ?? (
          <CircleDotIcon className="size-4 text-muted-foreground" />
        )}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          <div className="flex min-w-0 items-center gap-2 truncate text-xs">
            {props.title}
          </div>
          {props.badges ? (
            <div className="flex min-w-0 flex-wrap items-center gap-1">
              {props.badges}
            </div>
          ) : null}
        </div>
        {props.subtitle ? (
          <div className="mt-1 min-w-0 truncate text-xs text-muted-foreground">
            {props.subtitle}
          </div>
        ) : null}
      </div>
      {props.meta ? (
        <div className="ml-auto hidden shrink-0 items-center gap-2 text-xs text-muted-foreground md:flex">
          {props.meta}
        </div>
      ) : null}
      {props.actions ? (
        <div className="flex shrink-0 items-center gap-2 opacity-0 transition-opacity group-hover/item:opacity-100 group-focus-within/item:opacity-100">
          {props.actions}
        </div>
      ) : null}
    </>
  )

  if (props.onClick) {
    return (
      <button
        type="button"
        onClick={props.onClick}
        className={cn(
          "group/item -ml-[18px] flex w-[calc(100%+18px)] items-center gap-3 py-2 pl-3 pr-3 text-left transition-colors hover:bg-muted/50",
          props.isSelected && "bg-muted/60"
        )}
      >
        {content}
      </button>
    )
  }

  return (
    <div className="group/item -ml-[18px] flex w-[calc(100%+18px)] items-center gap-3 py-2 pl-3 pr-3 transition-colors hover:bg-muted/50">
      {content}
    </div>
  )
}

export const FeedRow = SpmCompactRow

export function SmallBadge(props: {
  children: ReactNode
  icon?: ComponentType<{ className?: string }>
  variant?: BadgeVariant
}) {
  const Icon = props.icon
  return (
    <Badge
      variant={props.variant ?? "secondary"}
      className="h-5 max-w-[220px] px-2 text-[10px] font-normal"
    >
      {Icon ? <Icon className="mr-1 size-3 shrink-0" /> : null}
      <span className="truncate">{props.children}</span>
    </Badge>
  )
}

function shortTimeAgo(date: Date): string {
  const diffMs = Date.now() - date.getTime()
  const diffMins = Math.floor(diffMs / 60_000)
  const diffHours = Math.floor(diffMs / 3_600_000)
  const diffDays = Math.floor(diffMs / 86_400_000)
  const diffWeeks = Math.floor(diffDays / 7)
  const diffMonths = Math.floor(diffDays / 30)

  if (diffMins < 1) return "now"
  if (diffMins < 60) return `${diffMins}m`
  if (diffHours < 24) return `${diffHours}h`
  if (diffDays < 7) return `${diffDays}d`
  if (diffDays < 30) return `${diffWeeks}w`
  return `${diffMonths}mo`
}

const TIMESTAMP_BADGE_CLASS = "h-5 cursor-default px-2 text-[10px] font-normal"

export function SpmTimestamp(props: {
  icon?: LucideIcon
  label: string
  value: string | null | undefined
}) {
  const Icon = props.icon ?? RefreshCwIcon
  if (!props.value) {
    return (
      <Badge variant="secondary" className={TIMESTAMP_BADGE_CLASS}>
        <Icon className="mr-1 size-3" />
        {props.label} never
      </Badge>
    )
  }

  const date = new Date(props.value)
  if (Number.isNaN(date.getTime())) {
    return (
      <Badge variant="secondary" className={TIMESTAMP_BADGE_CLASS}>
        <Icon className="mr-1 size-3" />
        {props.label} unknown
      </Badge>
    )
  }

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge variant="secondary" className={TIMESTAMP_BADGE_CLASS}>
            <Icon className="mr-1 size-3" />
            {props.label} {shortTimeAgo(date)}
          </Badge>
        </TooltipTrigger>
        <TooltipContent>
          {props.label}: {date.toLocaleString()}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

export const SpmSeenAtIcon = EyeIcon
export const SpmUpdatedAtIcon = RefreshCwIcon
