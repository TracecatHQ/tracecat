"use client"

import "react18-json-view/src/style.css"

import React from "react"
import {
  BanIcon,
  CalendarClockIcon,
  CheckCheckIcon,
  CopyIcon,
  PlusCircleIcon,
  SaveIcon,
  SettingsIcon,
  WebhookIcon,
} from "lucide-react"

import { Schedule, Webhook, Workflow } from "@/types/schemas"
import { useUpdateWebhook } from "@/lib/hooks"
import { copyToClipboard } from "@/lib/utils"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
import { Switch } from "@/components/ui/switch"
import {
  Table,
  TableBody,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { getIcon } from "@/components/icons"
import {
  TriggerNodeData,
  TriggerTypename,
} from "@/components/workspace/canvas/trigger-node"

export function TriggerPanel({
  workflow,
}: {
  nodeData: TriggerNodeData
  workflow: Workflow
}) {
  return (
    <div className="size-full overflow-auto">
      <div className="grid grid-cols-3">
        <div className="col-span-2 overflow-hidden">
          <h3 className="p-4">
            <div className="flex w-full items-center space-x-4">
              {getIcon(TriggerTypename, {
                className: "size-10 p-2",
                flairsize: "md",
              })}
              <div className="flex w-full flex-1 justify-between space-x-12">
                <div className="flex flex-col">
                  <div className="flex w-full items-center justify-between text-xs font-medium leading-none">
                    <div className="flex w-full">Trigger</div>
                  </div>
                  <p className="mt-2 text-xs text-muted-foreground">
                    Workflow Triggers
                  </p>
                </div>
              </div>
            </div>
          </h3>
        </div>
        <div className="flex justify-end space-x-2 p-4">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button size="icon" disabled>
                <SaveIcon className="size-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Save</TooltipContent>
          </Tooltip>
        </div>
      </div>
      <Separator />
      {/* Metadata */}
      <Accordion
        type="multiple"
        defaultValue={[
          "trigger-settings",
          "trigger-webhooks",
          "trigger-schedules",
        ]}
      >
        {/* General */}
        <AccordionItem value="trigger-settings">
          <AccordionTrigger className="px-4 text-xs font-bold tracking-wide">
            <div className="flex items-center">
              <SettingsIcon className="mr-3 size-4" />
              <span>General</span>
            </div>
          </AccordionTrigger>
          <AccordionContent>
            <div className="my-4 space-y-2 px-4">
              <GeneralControls
                entrypointRef={workflow.entrypoint ?? undefined}
              />
            </div>
          </AccordionContent>
        </AccordionItem>

        {/* Webhooks */}
        <AccordionItem value="trigger-webhooks">
          <AccordionTrigger className="px-4 text-xs font-bold tracking-wide">
            <div className="flex items-center">
              <WebhookIcon className="mr-3 size-4" />
              <span>Webhook</span>
            </div>
          </AccordionTrigger>
          <AccordionContent>
            <div className="my-4 space-y-2 px-4">
              <WebhookControls
                webhook={workflow.webhook}
                workflowId={workflow.id}
              />
            </div>
          </AccordionContent>
        </AccordionItem>

        {/* Schedules */}
        <AccordionItem value="trigger-schedules">
          <AccordionTrigger className="px-4 text-xs font-bold tracking-wide">
            <div className="flex items-center">
              <CalendarClockIcon className="mr-3 size-4" />
              <span>Schedules</span>
            </div>
          </AccordionTrigger>
          <AccordionContent>
            <div className="my-4 space-y-2 px-4">
              <ScheduleControls schedules={workflow.schedules} />
            </div>
          </AccordionContent>
        </AccordionItem>
      </Accordion>
    </div>
  )
}

export function GeneralControls({ entrypointRef }: { entrypointRef?: string }) {
  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label className="flex items-center gap-2 text-xs text-muted-foreground">
          <span>Entrypoint</span>
          {entrypointRef ? (
            <CopyButton
              value={entrypointRef}
              toastMessage="Copied entrypoint ID to clipboard"
            />
          ) : (
            <BanIcon className="size-3 text-muted-foreground" />
          )}
        </Label>
        <div className="rounded-md border shadow-sm">
          <Input
            name="entrypointId"
            className="rounded-md border-none text-xs shadow-none"
            value={entrypointRef || "No entrypoint"}
            readOnly
            disabled
          />
        </div>
      </div>
    </div>
  )
}

export function WebhookControls({
  webhook: { url, status },
  workflowId,
}: {
  webhook: Webhook
  workflowId: string
}) {
  const { mutateAsync } = useUpdateWebhook(workflowId)
  const onCheckedChange = async (checked: boolean) => {
    await mutateAsync({
      status: checked ? "online" : "offline",
    })
  }
  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label className="flex items-center gap-2 text-xs text-muted-foreground">
          <span>Toggle Webhook</span>
        </Label>
        <Switch
          checked={status === "online"}
          onCheckedChange={onCheckedChange}
          className="data-[state=checked]:bg-emerald-500"
        />
      </div>
      <div className="space-y-2">
        <Label className="flex items-center gap-2 text-xs text-muted-foreground">
          <span>URL</span>
          <CopyButton value={url} toastMessage="Copied URL to clipboard" />
        </Label>
        <div className="rounded-md border shadow-sm">
          <Input
            name="url"
            defaultValue={url}
            className="rounded-md border-none text-xs shadow-none"
            readOnly
            disabled
          />
        </div>
      </div>
    </div>
  )
}
function CopyButton({
  value,
  toastMessage,
}: {
  value: string
  toastMessage: string
}) {
  const [copied, setCopied] = React.useState(false)
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          className="group m-0 size-4 p-0"
          onClick={() => {
            copyToClipboard({
              value,
              message: toastMessage,
            })
            setCopied(true)
            setTimeout(() => setCopied(false), 2000)
          }}
        >
          {copied ? (
            <CheckCheckIcon className="size-3 text-muted-foreground" />
          ) : (
            <CopyIcon className="size-3 text-muted-foreground" />
          )}
        </Button>
      </TooltipTrigger>
      <TooltipContent>Copy</TooltipContent>
    </Tooltip>
  )
}
export function ScheduleControls({ schedules }: { schedules: Schedule[] }) {
  return (
    <div className="rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="h-8 text-center text-xs">
              <div className="flex items-center justify-center gap-1">
                <WebhookIcon className="size-3" />
                <span>Schedules</span>
              </div>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {schedules.map(({ id, cron }) => (
            <TableRow key={id}>
              <TableCell>{cron}</TableCell>
            </TableRow>
          ))}
        </TableBody>
        <TableFooter className="flex w-full justify-center text-muted-foreground">
          <Button
            variant="ghost"
            size="sm"
            className="flex w-full items-center justify-center gap-2"
          >
            <PlusCircleIcon className="size-4" />
            <span>Add Schedule</span>
          </Button>
        </TableFooter>
      </Table>
    </div>
  )
}

// ;<div className="flex w-full max-w-sm items-center rounded-md border">
//   <Input
//     name="url"
//     defaultValue={url}
//     className="rounded-r-none border-none pr-0"
//     readOnly
//     disabled
//   />
//   <Button variant="ghost" className="group rounded-l-none border-l shadow-sm">
//     <CopyIcon className="size-4 text-muted-foreground/50 group-hover:text-foreground" />
//   </Button>
// </div>
