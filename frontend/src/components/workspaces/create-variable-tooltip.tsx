"use client"

import { Info } from "lucide-react"

import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"

export function CreateVariableTooltip() {
  return (
    <div className="flex items-center">
      <HoverCard openDelay={100} closeDelay={100}>
        <HoverCardTrigger asChild className="hover:border-none">
          <Info className="mr-1 size-4 stroke-muted-foreground" />
        </HoverCardTrigger>
        <HoverCardContent
          className="w-auto max-w-[500px] p-3 font-mono text-xs tracking-tight"
          side="left"
          sideOffset={20}
        >
          <div className="w-full space-y-4">
            <div className="w-full items-center text-start text-muted-foreground ">
              Create a variable that can have multiple key-value pairs. You can
              reference these variables in your workflows through{" "}
            </div>
            <div className="rounded-md border bg-muted-foreground/10 p-2">
              <pre className="text-xs text-foreground/70">
                {"${{ VARS.<my_variable>.<key> }}"}
              </pre>
            </div>
            <div className="w-full items-center text-start text-muted-foreground ">
              <span>
                For example, if I have a variable called with key API_URL, I can
                create a variable named `api_config` and reference this as{" "}
              </span>
            </div>
            <div className="flex w-full flex-col text-muted-foreground ">
              <div className="rounded-md border bg-muted-foreground/10 p-2">
                <pre className="text-xs text-foreground/70">
                  {"${{ VARS.api_config.API_URL }}"}
                </pre>
              </div>
            </div>
          </div>
        </HoverCardContent>
      </HoverCard>
      <span className="text-sm text-muted-foreground">
        Define a variable with one or more key-value pairs.
      </span>
    </div>
  )
}
