import React from "react"
import { ActionRead } from "@/client"
import {
  AlertTriangle,
  ArrowRight,
  Edit,
  MessagesSquare,
  Repeat,
} from "lucide-react"

import { Label } from "@/components/ui/label"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"

function parseForEach(forEach: string): {
  variable: string
  array: string
} | null {
  // match ${{ for <variable> in <array> }}
  const match = /^\$\{\{.*for\s+(.*)\s+in\s+(.*)\}\}$/.exec(forEach)
  if (!match) {
    return null
  }
  const [, variable, array] = match
  return { variable, array }
}

export function ForEachTooltip({ forEach }: { forEach: string }) {
  const parsed = parseForEach(forEach)
  if (parsed) {
    return (
      <div className="flex items-center space-x-1 text-xs">
        <span className="inline-block rounded-sm border border-input bg-muted-foreground/10 px-0.5 py-0 font-mono tracking-tight text-foreground/70">
          {parsed.array}
        </span>
        <ArrowRight className="size-3" />
        <span className="inline-block rounded-sm border border-input bg-muted-foreground/10 px-0.5 py-0 font-mono tracking-tight text-foreground/70">
          {parsed.variable}
        </span>
      </div>
    )
  }
  return (
    <span className="flex items-center space-x-1 text-xs text-foreground/70">
      <AlertTriangle className="size-3 fill-red-500 stroke-white" />
      <span className="inline-block font-mono tracking-tight text-foreground/70">
        Invalid For Loop
      </span>
    </span>
  )
}

export function ForEachEffect({
  forEach,
  onClick,
}: {
  forEach?: string | string[]
  onClick: () => void
}) {
  const [open, setOpen] = React.useState(false)

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <div className="group" onClick={() => setOpen(!open)}>
          <div className="flex size-6 items-center justify-center rounded-lg bg-indigo-400 shadow-sm hover:bg-indigo-400/80 group-hover:cursor-pointer">
            <Repeat className="size-3 stroke-muted" strokeWidth={2.5} />
          </div>
        </div>
      </PopoverTrigger>
      <PopoverContent
        className="w-auto rounded-lg p-0 shadow-sm"
        side="left"
        align="start"
        alignOffset={0}
        avoidCollisions={false}
        onInteractOutside={(e) => {
          // Prevent the popover from closing when clicking outside
          e.preventDefault()
        }}
      >
        <div className="w-full border-b bg-muted-foreground/5 px-3 py-[2px]">
          <div className="flex items-center justify-between">
            <Label className="flex items-center text-xs text-muted-foreground">
              <span className="font-medium">For Loop</span>
            </Label>
            <span className="my-px ml-auto flex items-center space-x-2">
              <Tooltip>
                <TooltipTrigger asChild>
                  <Edit
                    className="size-3 stroke-muted-foreground/70 hover:cursor-pointer"
                    onClick={(e) => {
                      e.stopPropagation()
                      onClick()
                    }}
                  />
                </TooltipTrigger>
                <TooltipContent>Open editor</TooltipContent>
              </Tooltip>
            </span>
          </div>
        </div>
        <div className="flex flex-col gap-2 p-2">
          <div className="flex items-center space-x-1 text-xs text-foreground/70">
            <span>Collection</span>
            <ArrowRight className="size-3" />
            <span>Loop variable</span>
          </div>

          {typeof forEach === "string" ? (
            <ForEachTooltip forEach={forEach} />
          ) : Array.isArray(forEach) && forEach.length > 0 ? (
            <div className="flex flex-col space-y-1">
              {forEach.map((statement) => (
                <ForEachTooltip key={statement} forEach={statement} />
              ))}
            </div>
          ) : (
            <span className="flex items-center space-x-1 text-xs text-foreground/70">
              <AlertTriangle className="size-3" />
              <span className="inline-block font-mono tracking-tight text-foreground/70">
                Invalid For Loop Type
              </span>
            </span>
          )}
        </div>
      </PopoverContent>
    </Popover>
  )
}

export function InteractionEffect({
  interaction,
  onClick,
}: {
  interaction: ActionRead["interaction"]
  onClick: () => void
}) {
  if (!interaction) {
    return null
  }

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <div className="group" onClick={onClick}>
            <div className="flex size-6 items-center justify-center rounded-lg bg-amber-400 shadow-sm hover:bg-amber-400/80 group-hover:cursor-pointer">
              <MessagesSquare
                className="size-3 stroke-muted"
                strokeWidth={2.5}
              />
            </div>
          </div>
        </TooltipTrigger>
        <TooltipContent>
          <span className="capitalize">{interaction.type}</span>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
