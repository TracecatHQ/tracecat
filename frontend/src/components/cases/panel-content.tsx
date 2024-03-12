"use client"

import React from "react"
import SyntaxHighlighter from "react-syntax-highlighter"
import { atomOneLight } from "react-syntax-highlighter/dist/esm/styles/hljs"

import { Case } from "@/types/schemas"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import {
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { StatusBadge } from "@/components/badges"
import { statuses } from "@/components/cases/data/categories"

interface CasePanelContentProps {
  caseData: Case
}

function parseCaseData(caseData: Case) {
  const currentStatus = statuses.find(
    (status) => status.value === caseData.status
  )
  const payload = JSON.stringify(JSON.parse(caseData.payload), null, 2)
  const context = JSON.stringify(caseData.context, null, 2)
  return {
    parsedCaseData: caseData,
    currentStatus,
    payload,
    context,
  }
}

export function CasePanelContent({ caseData }: CasePanelContentProps) {
  const { parsedCaseData, currentStatus, payload, context } =
    parseCaseData(caseData)
  const { id, title, priority, malice } = parsedCaseData
  return (
    <ScrollArea className="h-full">
      <div className="flex flex-col space-y-4">
        <SheetHeader>
          <small>Case #{id}</small>
          <div className="flex items-center justify-between">
            <SheetTitle className="text-md">{title}</SheetTitle>
            <Select defaultValue={currentStatus?.value}>
              <SelectTrigger className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  <SelectLabel>Status</SelectLabel>
                  {statuses.map((status) => (
                    <SelectItem key={status.value} value={status.value}>
                      <span className="flex items-center">
                        {status.icon && (
                          <status.icon className="mr-2 h-4 w-4 text-muted-foreground" />
                        )}
                        {status.label}
                      </span>
                    </SelectItem>
                  ))}
                </SelectGroup>
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-center space-x-2">
            <StatusBadge status={priority ?? ""}>
              priority: {priority}
            </StatusBadge>
            <StatusBadge status={malice ?? ""}>{malice}</StatusBadge>
          </div>
        </SheetHeader>
        <Separator />
        <SheetDescription className="flex flex-col space-y-4">
          <div className="flex flex-col space-y-2">
            <div className="text-md">Payload</div>
            <SyntaxHighlighter language="json" style={atomOneLight}>
              {payload}
            </SyntaxHighlighter>
          </div>
          <div className="flex flex-col space-y-2">
            <div className="text-md">Context</div>
            <pre>
              <SyntaxHighlighter language="json" style={atomOneLight}>
                {context}
              </SyntaxHighlighter>
            </pre>
          </div>
          <div className="text-md">Runbook</div>
          <div className="text-md">Metrics</div>
        </SheetDescription>
      </div>
    </ScrollArea>
  )
}
