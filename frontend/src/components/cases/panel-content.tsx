"use client"

import React from "react"
import SyntaxHighlighter from "react-syntax-highlighter"
import { atomOneDark } from "react-syntax-highlighter/dist/esm/styles/hljs"

import { Case } from "@/types/schemas"
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
import { SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { StatusBadge } from "@/components/badges"
import { statuses } from "@/components/cases/data/categories"

type TStatus = (typeof statuses)[number]

interface CasePanelContentProps {
  currentCase: Case
}

export function CasePanelContent({
  currentCase: {
    id,
    title,
    priority,
    malice,
    payload,
    context,
    status: caseStatus,
  },
}: CasePanelContentProps) {
  const currentStatus = statuses.find((status) => status.value === caseStatus)
  const renderedPayload = JSON.stringify(payload, null, 2)
  const renderedContext = context
    ? JSON.stringify(context, null, 2)
    : "No context available"

  return (
    <div className="flex flex-col space-y-4 overflow-auto">
      <SheetHeader>
        <small>Case #{id}</small>
        <div className="flex items-center justify-between">
          <SheetTitle className="text-md">{title}</SheetTitle>
          <CaseStatusSelect status={currentStatus} />
        </div>
        <div className="flex items-center space-x-2">
          <StatusBadge status={priority ?? ""}>
            priority: {priority}
          </StatusBadge>
          <StatusBadge status={malice ?? ""}>{malice}</StatusBadge>
        </div>
      </SheetHeader>
      <Separator />
      <div className="flex flex-col space-y-4 text-sm">
        <div className="flex flex-col space-y-2">
          <h5 className="text-xs font-semibold">Payload</h5>
          <CodeContent data={renderedPayload} />
        </div>
        <div className="flex flex-col space-y-2">
          <h5 className="text-xs font-semibold">Context</h5>
          <pre>
            <CodeContent data={renderedContext} />
          </pre>
        </div>
        <h5 className="text-xs font-semibold">Actions</h5>
      </div>
    </div>
  )
}

function CaseStatusSelect({ status }: { status?: TStatus }) {
  return (
    <Select defaultValue={status?.value}>
      <SelectTrigger className="w-40 focus:ring-0">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectGroup>
          <SelectLabel>Status</SelectLabel>
          {statuses.map((status) => (
            <SelectItem key={status.value} value={status.value}>
              <span className="flex items-center text-xs">
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
  )
}

function CodeContent({ data }: { data: string }) {
  return (
    <SyntaxHighlighter
      language="json"
      style={atomOneDark}
      wrapLines
      customStyle={{
        width: "100%",
        maxWidth: "100%",
        overflowX: "auto",
      }}
      codeTagProps={{
        className:
          "text-xs text-background rounded-md max-w-full overflow-auto",
      }}
      className="w-full max-w-full overflow-auto rounded-md p-4"
    >
      {data}
    </SyntaxHighlighter>
  )
}
