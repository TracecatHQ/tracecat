"use client"

import {
  ArrowLeftIcon,
  CircleDotIcon,
  SearchIcon,
  ShieldCheckIcon,
} from "lucide-react"
import Link from "next/link"
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
        <div className="ml-auto flex items-center gap-2">{props.action}</div>
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
          <div className="ml-auto flex items-center gap-2">
            <span className="text-xs text-muted-foreground">
              {props.count} {props.countLabel}
            </span>
          </div>
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

export function SpmDetailShell(props: {
  backHref: string
  backLabel: string
  children: ReactNode
  icon: ComponentType<{ className?: string }>
  subtitle?: ReactNode
  title: ReactNode
}) {
  const Icon = props.icon
  return (
    <div className="flex size-full flex-col">
      <header className="shrink-0 border-b">
        <div className="flex h-10 items-center pl-3 pr-4">
          <Link
            href={props.backHref}
            className="inline-flex items-center gap-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
          >
            <ArrowLeftIcon className="size-4" />
            {props.backLabel}
          </Link>
        </div>
        <div className="flex min-h-10 items-center gap-3 border-t pl-3 pr-4">
          <div className="flex h-7 w-7 shrink-0 items-center justify-center">
            <Icon className="size-4 text-muted-foreground" />
          </div>
          <div className="min-w-0">
            <div className="truncate text-sm font-medium">{props.title}</div>
            {props.subtitle ? (
              <div className="mt-0.5 text-xs text-muted-foreground">
                {props.subtitle}
              </div>
            ) : null}
          </div>
        </div>
      </header>
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

export function FeedRow(props: {
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
          <div className="min-w-0 truncate text-sm font-medium">
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
        <div className="hidden shrink-0 items-center gap-2 text-xs text-muted-foreground md:flex">
          {props.meta}
        </div>
      ) : null}
      {props.actions ? (
        <div className="flex shrink-0 items-center gap-2">{props.actions}</div>
      ) : null}
    </>
  )

  if (props.onClick) {
    return (
      <button
        type="button"
        onClick={props.onClick}
        className={cn(
          "flex w-full items-center gap-3 border-b px-3 py-2 text-left last:border-b-0 hover:bg-muted/50",
          props.isSelected && "bg-muted/60"
        )}
      >
        {content}
      </button>
    )
  }

  return (
    <div className="flex items-center gap-3 border-b px-3 py-2 last:border-b-0 hover:bg-muted/50">
      {content}
    </div>
  )
}

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
