"use client"

import React from "react"
import { useParams } from "next/navigation"
import { useSession } from "@/providers/session"
import { Bell, ShieldQuestion, Smile, TagsIcon } from "lucide-react"
import SyntaxHighlighter from "react-syntax-highlighter"
import { atomOneDark } from "react-syntax-highlighter/dist/esm/styles/hljs"

import { CasePriorityType, CaseStatusType } from "@/types/schemas"
import { useCaseEvents, usePanelCase } from "@/lib/hooks"
import { cn } from "@/lib/utils"
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
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { Timeline } from "@/components/timeline"

type TStatus = (typeof statuses)[number]
type TPriority = (typeof priorities)[number]

interface CasePanelContentProps {
  caseId: string
}

export function CasePanelContent({ caseId }: CasePanelContentProps) {
  const session = useSession()
  const { workflowId } = useParams<{
    workflowId: string
  }>()
  const { caseData, caseIsLoading, caseError, mutateCaseAsync } = usePanelCase(
    session,
    workflowId,
    caseId
  )
  const { mutateCaseEventsAsync } = useCaseEvents(session, workflowId, caseId)

  if (caseIsLoading) {
    return <CenteredSpinner />
  }
  if (caseError || !caseData) {
    return (
      <AlertNotification
        level="error"
        message={caseError?.message ?? "Error occurred"}
      />
    )
  }
  const {
    id,
    title,
    status: caseStatus,
    priority,
    malice,
    action,
    tags,
    payload,
    context,
    suppression,
  } = caseData

  const handleStatusChange = async (newStatus: CaseStatusType) => {
    console.log("Updating status to", newStatus)
    await mutateCaseAsync({
      ...caseData,
      id: caseId,
      status: newStatus,
    })
    await mutateCaseEventsAsync({
      type: "status_changed",
      data: {
        status: newStatus,
      },
    })
  }

  const handlePriorityChange = async (newPriority: CasePriorityType) => {
    console.log("Updating priority to", newPriority)
    await mutateCaseAsync({
      ...caseData,
      id: caseId,
      priority: newPriority,
    })
    await mutateCaseEventsAsync({
      type: "priority_changed",
      data: {
        priority: newPriority,
      },
    })
  }

  const currentStatus = statuses.find((status) => status.value === caseStatus)!
  const currentPriority = priorities.find((p) => p.value === priority)!
  return (
    <TooltipProvider delayDuration={300}>
      <div className="flex h-full flex-col">
        <div className="my-6 space-y-4">
          <SheetHeader>
            <small className="text-xs text-muted-foreground">Case #{id}</small>
            <div className="flex items-center justify-between">
              <SheetTitle className="text-lg">{title}</SheetTitle>
              <div className="flex items-center gap-2">
                <PrioritySelect
                  priority={currentPriority}
                  onValueChange={handlePriorityChange}
                />
                <StatusSelect
                  status={currentStatus}
                  onValueChange={handleStatusChange}
                />
              </div>
            </div>
            <div className="flex flex-col space-y-2 text-muted-foreground">
              <div className="flex items-center gap-4">
                <div className="flex items-center space-x-2">
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Bell className="size-4" />
                    </TooltipTrigger>
                    <TooltipContent side="top" sideOffset={5}>
                      Priority
                    </TooltipContent>
                  </Tooltip>
                  <StatusBadge status={priority}>
                    <currentPriority.icon
                      className="stroke-inherit/5 size-3"
                      strokeWidth={3}
                    />
                    <span className="text-xs">{currentPriority.label}</span>
                  </StatusBadge>
                </div>
                <div className="flex items-center space-x-2">
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Smile className="size-4" />
                    </TooltipTrigger>
                    <TooltipContent side="top" sideOffset={5}>
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
                    <TooltipContent side="top" sideOffset={5}>
                      Action
                    </TooltipContent>
                  </Tooltip>
                  <StatusBadge status={action}>{action}</StatusBadge>
                </div>
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
                    <StatusBadge key={idx}>
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
          <div className="flex-1">
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div className="col-span-2 flex min-h-[20vh] flex-1 flex-col space-y-2">
                <h5 className="text-xs font-semibold">Payload</h5>
                <div className="flex-1 space-y-2">
                  <CodeContent data={payload} />
                </div>
              </div>
              <div id="body" className="col-span-2 grid grid-cols-2 gap-4">
                <div id="body-left" className="col-span-1 space-y-2">
                  <h5 className="text-xs font-semibold">Context</h5>
                  <Card
                    className={cn(
                      "flex min-h-[10vh] p-4 shadow-sm",
                      context.length === 0 && "items-center"
                    )}
                  >
                    <LabelsTable
                      keyName="key"
                      valueName="value"
                      labels={context}
                      emptyMessage="No context available"
                    />
                  </Card>
                </div>
                <div id="body-right" className="col-span-1 space-y-2">
                  <h5 className="text-xs font-semibold">Suppressions</h5>
                  <Card
                    className={cn(
                      "flex min-h-[10vh] p-4 shadow-sm",
                      suppression.length === 0 && "items-center"
                    )}
                  >
                    <LabelsTable
                      keyName="condition"
                      valueName="result"
                      labels={suppression}
                      emptyMessage="No suppressions available"
                    />
                  </Card>
                </div>
                <div className="col-span-2 space-y-4">
                  <h5 className="text-xs font-semibold">Activity</h5>
                  <Timeline workflowId={workflowId} caseId={caseId} />
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </TooltipProvider>
  )
}

interface StatusSelectProps {
  status: TStatus
  onValueChange: (status: CaseStatusType) => void
}
function StatusSelect({ status, onValueChange }: StatusSelectProps) {
  return (
    <Select defaultValue={status?.value} onValueChange={onValueChange}>
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

interface PrioritySelectProps {
  priority: TPriority
  onValueChange: (status: CasePriorityType) => void
}
function PrioritySelect({
  priority: { value },
  onValueChange,
}: PrioritySelectProps) {
  return (
    <Select defaultValue={value} onValueChange={onValueChange}>
      <SelectTrigger className="w-40 focus:ring-0">
        <SelectValue />
      </SelectTrigger>
      <SelectContent className="flex w-full">
        <SelectGroup>
          <SelectLabel>Status</SelectLabel>
          {priorities.map(({ label, value, icon: Icon }) => {
            return (
              <SelectItem key={value} value={value} className="flex w-full">
                <StatusBadge
                  status={value}
                  className="inline-flex w-full border-none"
                >
                  <Icon
                    className="stroke-inherit/5 size-3 flex-1"
                    strokeWidth={3}
                  />
                  <span className="text-xs">{label}</span>
                </StatusBadge>
              </SelectItem>
            )
          })}
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
