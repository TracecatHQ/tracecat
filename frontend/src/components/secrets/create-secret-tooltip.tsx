"use client"

import React from "react"
import { Info } from "lucide-react"

import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"

export function CreateSecretTooltip() {
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
              Create a secret that can have multiple key-value credential pairs.
              You can reference these secrets in your workflows through{" "}
            </div>
            <div className="rounded-md border bg-muted-foreground/10 p-2">
              <pre className="text-xs text-foreground/70">
                {"${{ SECRETS.<my_secret>.<key> }}"}
              </pre>
            </div>
            <div className="w-full items-center text-start text-muted-foreground ">
              <span>
                For example, if I have a secret called with key GH_ACCESS_TOKEN,
                I can create a secret named `my_github_secret` and reference
                this as{" "}
              </span>
            </div>
            <div className="flex w-full flex-col text-muted-foreground ">
              <div className="rounded-md border bg-muted-foreground/10 p-2">
                <pre className="text-xs text-foreground/70">
                  {"${{ SECRETS.my_github_secret.GH_ACCESS_TOKEN }}"}
                </pre>
              </div>
            </div>
          </div>
        </HoverCardContent>
      </HoverCard>
      <span className="text-sm text-muted-foreground">
        Define a secret with one or more key-value pairs.
      </span>
    </div>
  )
}
