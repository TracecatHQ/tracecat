import React, { useRef } from "react"
import SyntaxHighlighter from "react-syntax-highlighter"
import { atomOneLight } from "react-syntax-highlighter/dist/esm/styles/hljs"

import { type Case } from "@/types/schemas"
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
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet"
import { StatusBadge } from "@/components/badges"
import { statuses } from "@/components/cases/data/categories"

interface CasePanelProps extends Partial<Case> {
  isOpen: boolean
}

export function CasePanel({ isOpen, ...props }: CasePanelProps) {
  const currentStatus = statuses.find((status) => status.value === props.status)
  const payload = JSON.stringify(JSON.parse(props.payload ?? "{}"), null, 2)
  const context = JSON.stringify(props.context, null, 2)
  const codeBlockRef = useRef<HTMLElement | null>(null)

  return (
    <Sheet open={isOpen} defaultOpen={false}>
      <SheetTrigger>
        <div>Case</div>
      </SheetTrigger>
      <SheetContent className="w-[60vw] sm:w-[60vw] sm:max-w-none">
        <div className="flex flex-col space-y-4">
          <SheetHeader>
            <small>Case #{props.id}</small>
            <div className="flex items-center justify-between">
              <SheetTitle className="text-md">{props.title}</SheetTitle>
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
              <StatusBadge status={props.priority ?? ""}>
                priority: {props.priority}
              </StatusBadge>
              <StatusBadge status={props.malice ?? ""}>
                {props.malice}
              </StatusBadge>
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
      </SheetContent>
    </Sheet>
  )
}
