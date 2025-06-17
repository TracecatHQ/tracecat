"use client"

import React from "react"
import { InfoIcon } from "lucide-react"

import { FormLabel } from "@/components/ui/form"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"

export function ControlFlowField({
  label,
  tooltip,
  description,
  children,
}: {
  label?: string
  tooltip?: React.ReactNode
  description?: string
  children: React.ReactNode
}) {
  return (
    <div className="flex flex-col space-y-2">
      <FormLabel className="flex items-center gap-2 text-xs font-medium">
        <span>{label}</span>
      </FormLabel>
      <div className="mb-2 flex items-center">
        {tooltip && (
          <HoverCard openDelay={100} closeDelay={100}>
            <HoverCardTrigger asChild className="hover:border-none">
              <InfoIcon className="mr-1 size-3 stroke-muted-foreground" />
            </HoverCardTrigger>
            <HoverCardContent
              className="w-auto max-w-[500px] p-3 font-mono text-xs tracking-tight"
              side="left"
              sideOffset={20}
            >
              {tooltip}
            </HoverCardContent>
          </HoverCard>
        )}

        <span className="text-xs text-muted-foreground">{description}</span>
      </div>
      {children}
    </div>
  )
}
