"use client"

import React from "react"
import { Bell, ShieldQuestion, Smile, TagsIcon } from "lucide-react"
import SyntaxHighlighter from "react-syntax-highlighter"
import { atomOneDark } from "react-syntax-highlighter/dist/esm/styles/hljs"

import { Case } from "@/types/schemas"
import { Card } from "@/components/ui/card"
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
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { StatusBadge } from "@/components/badges"
import { priorities, statuses } from "@/components/cases/data/categories"
import { AIGeneratedFlair } from "@/components/flair"
import { LabelsTable } from "@/components/labels-table"

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
    action,
    suppression,
    tags,
  },
}: CasePanelContentProps) {
  const currentStatus = statuses.find((status) => status.value === caseStatus)

  const { label, icon: Icon } = priorities.find((p) => p.value === priority)!
  return (
    <TooltipProvider delayDuration={300}>
      <div className="flex flex-col space-y-4 overflow-auto">
        <SheetHeader>
          <small className="text-xs text-muted-foreground">Case #{id}</small>
          <div className="flex items-center justify-between">
            <SheetTitle className="text-lg">{title}</SheetTitle>
            <CaseStatusSelect status={currentStatus} />
          </div>
          <div className="flex flex-col space-y-2 text-muted-foreground">
            <div className="flex items-center space-x-2">
              <Tooltip>
                <TooltipTrigger asChild>
                  <Bell className="size-4" />
                </TooltipTrigger>
                <TooltipContent side="left" sideOffset={20}>
                  Priority
                </TooltipContent>
              </Tooltip>
              <StatusBadge status={priority}>
                <Icon className="stroke-inherit/5 size-3" strokeWidth={3} />
                <span className="text-xs">{label}</span>
              </StatusBadge>
            </div>
            <div className="flex items-center space-x-2">
              <Tooltip>
                <TooltipTrigger asChild>
                  <Smile className="size-4" />
                </TooltipTrigger>
                <TooltipContent side="left" sideOffset={20}>
                  Malice
                </TooltipContent>
              </Tooltip>
              <StatusBadge status={malice}>{malice}</StatusBadge>
            </div>
            <div className="flex items-center space-x-2">
              <Tooltip>
                <TooltipTrigger asChild>
                  <ShieldQuestion className="size-4" />
                </TooltipTrigger>
                <TooltipContent side="left" sideOffset={20}>
                  Action
                </TooltipContent>
              </Tooltip>
              <StatusBadge status={action}>{action}</StatusBadge>
            </div>
            <div className="flex items-center space-x-2">
              <Tooltip>
                <TooltipTrigger asChild>
                  <TagsIcon className="size-4" />
                </TooltipTrigger>
                <TooltipContent side="left" sideOffset={20}>
                  Tags
                </TooltipContent>
              </Tooltip>
              {tags.length > 0 ? (
                tags.map((tag, idx) => (
                  <StatusBadge key={idx} className="flex items-center">
                    <AIGeneratedFlair isAIGenerated={tag.is_ai_generated}>
                      {tag.tag}: {tag.value}
                    </AIGeneratedFlair>
                  </StatusBadge>
                ))
              ) : (
                <span className="text-xs text-muted-foreground">No tags</span>
              )}
            </div>
          </div>
        </SheetHeader>
        <Separator />
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div className="col-span-2 flex flex-1 flex-col space-y-2">
            <h5 className="text-xs font-semibold">Payload</h5>
            <div className="flex-1 space-y-2">
              <CodeContent data={payload} />
            </div>
          </div>
          <div className="col-span-2 grid grid-cols-2 gap-4">
            <div className="col-span-1 space-y-2">
              <h5 className="text-xs font-semibold">Context</h5>
              <Card className="p-4 shadow-sm">
                <LabelsTable
                  keyName="key"
                  valueName="value"
                  labels={context}
                  emptyMessage="No context available"
                />
              </Card>
            </div>
            <div className="col-span-1 space-y-2">
              <h5 className="text-xs font-semibold">Suppressions</h5>
              <Card className="p-4 shadow-sm">
                <LabelsTable
                  keyName="condition"
                  valueName="result"
                  labels={suppression}
                  emptyMessage="No suppressions available"
                />
              </Card>
            </div>
          </div>
        </div>
      </div>
    </TooltipProvider>
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

function CodeContent({ data }: { data: Record<string, string> }) {
  return (
    <SyntaxHighlighter
      language="json"
      style={atomOneDark}
      showLineNumbers
      wrapLines
      customStyle={{
        height: "100%",
        width: "100%",
        maxWidth: "100%",
        overflowX: "auto",
      }}
      codeTagProps={{
        className:
          "text-xs text-background rounded-md max-w-full overflow-auto",
      }}
      className="no-scrollbar w-full max-w-full overflow-auto rounded-md p-4"
    >
      {JSON.stringify(data, null, 2)}
    </SyntaxHighlighter>
  )
}
