import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { Link2Icon } from "lucide-react"
import Link from "next/link"
import type React from "react"
import { Badge } from "@/components/ui/badge"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

export type WorkflowEventsListRow = {
  key: string
  label: string
  time: string
  icon: React.ReactNode
  selected?: boolean
  count?: number
  subflowLink?: string
  trailing?: React.ReactNode
  onSelect?: () => void
}

export function WorkflowEventsList({
  rows,
  className,
}: {
  rows: WorkflowEventsListRow[]
  className?: string
}) {
  return (
    <div className={cn("relative", className)}>
      {rows.length > 0 && (
        <div className="pointer-events-none absolute bottom-6 left-[22px] top-6 w-px bg-gray-300" />
      )}
      {rows.map((row) => {
        const isInteractive = Boolean(row.onSelect)
        return (
          <div key={row.key}>
            <div
              role={isInteractive ? "button" : undefined}
              tabIndex={isInteractive ? 0 : undefined}
              className={cn(
                "group flex h-11 items-center px-3 text-xs transition-all",
                isInteractive && "cursor-pointer hover:bg-muted/50",
                row.selected && "bg-muted-foreground/10"
              )}
              onClick={() => {
                row.onSelect?.()
              }}
              onKeyDown={(event) => {
                if (
                  isInteractive &&
                  (event.key === "Enter" || event.key === " ")
                ) {
                  event.preventDefault()
                  row.onSelect?.()
                }
              }}
            >
              <div className="relative z-10 mr-3 flex size-5 items-center justify-center">
                {row.icon}
              </div>
              <div className="flex min-w-0 flex-1 items-center justify-between gap-2">
                <div className="flex min-w-0 items-center gap-2 text-xs">
                  <div className="truncate text-foreground/70">{row.label}</div>
                  {row.count && row.count > 1 && (
                    <Badge
                      variant="secondary"
                      className="h-4 px-1.5 text-[10px] font-medium text-foreground/60"
                    >
                      {row.count}x
                    </Badge>
                  )}
                  {row.subflowLink && (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Link
                          href={row.subflowLink}
                          className="inline-flex text-foreground/70 hover:text-foreground"
                          onClick={(event) => {
                            event.stopPropagation()
                          }}
                          aria-label="View subflow run"
                        >
                          <Link2Icon className="size-4" />
                        </Link>
                      </TooltipTrigger>
                      <TooltipContent side="top">
                        <span>View subflow run</span>
                      </TooltipContent>
                    </Tooltip>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <div className="whitespace-nowrap text-foreground/70">
                    {row.time}
                  </div>
                  {row.trailing ?? (
                    <DotsHorizontalIcon className="size-4 text-foreground/70" />
                  )}
                </div>
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}
