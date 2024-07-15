"use client"

import "react18-json-view/src/style.css"

import React from "react"
import { schedulesCreateSchedule } from "@/client"
import { zodResolver } from "@hookform/resolvers/zod"
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
import { useForm } from "react-hook-form"

import { Schedule, Webhook, Workflow } from "@/types/schemas"
import { useUpdateWebhook } from "@/lib/hooks"
import { Duration, durationSchema, durationToISOString } from "@/lib/time"
import { copyToClipboard } from "@/lib/utils"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
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
import { toast } from "@/components/ui/use-toast"
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
              <ScheduleControls
                schedules={workflow.schedules}
                workflowId={workflow.id}
              />
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
export function ScheduleControls({
  schedules,
  workflowId,
}: {
  schedules: Schedule[]
  workflowId: string
}) {
  const handleCreateSchedule = async () => {
    await schedulesCreateSchedule({
      requestBody: {
        workflow_id: workflowId,
        every: "1h",
      },
    })
    console.log("Add schedule")
  }
  return (
    <div className="rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="h-8 text-center text-xs" colSpan={4}>
              <div className="flex items-center justify-center gap-1">
                <WebhookIcon className="size-3" />
                <span>Schedules</span>
              </div>
            </TableHead>
          </TableRow>
          <TableRow>
            <TableHead className="h-8 text-center text-xs">ID</TableHead>
            <TableHead className="h-8 text-center text-xs">Status</TableHead>
            <TableHead className="h-8 text-center text-xs">Every</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {schedules.map(({ id, status, inputs, every }) => (
            <Popover>
              <PopoverTrigger asChild>
                <TableRow key={id} className="text-xs text-muted-foreground">
                  <TableCell>{id}</TableCell>
                  <TableCell>{status}</TableCell>
                  <TableCell>{every}</TableCell>
                </TableRow>
              </PopoverTrigger>
              <PopoverContent className="p-3" side="left" align="start">
                <span className="text-xs font-semibold text-muted-foreground">
                  Inputs
                </span>
                <div className="rounded-md border bg-muted-foreground/20">
                  <pre>{JSON.stringify(inputs)}</pre>
                </div>
              </PopoverContent>
            </Popover>
          ))}
        </TableBody>
      </Table>
      <CreateScheduleDialog workflowId={workflowId} />
    </div>
  )
}

export function CreateScheduleDialog({ workflowId }: { workflowId: string }) {
  const form = useForm<Duration>({
    resolver: zodResolver(durationSchema),
  })
  const onSubmit = async (values: Duration) => {
    console.log("Create schedule", values)

    const every = durationToISOString(values)
    await schedulesCreateSchedule({
      requestBody: {
        workflow_id: workflowId,
        every,
      },
    })

    toast({
      title: "Schedule created",
      description: "The schedule has been created successfully.",
    })
  }
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="flex h-7 w-full items-center justify-center gap-2 text-muted-foreground"
        >
          <PlusCircleIcon className="size-4" />
          <span>Create Schedule</span>
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-[425px]">
        <DialogHeader>
          <DialogTitle>Create a new schedule</DialogTitle>
          <DialogDescription>
            Configure the schedule for the workflow. The workflow will not run
            immediately.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)}>
            <div className="grid grid-cols-2 gap-2">
              {["years", "months", "days", "hours", "minutes", "seconds"].map(
                (unit, idx) => (
                  <FormField
                    key={idx}
                    control={form.control}
                    name={unit as keyof Duration}
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel className="text-xs capitalize text-foreground/80">
                          {unit}
                        </FormLabel>
                        <FormControl>
                          <Input
                            type="number"
                            className="text-xs capitalize"
                            placeholder={unit}
                            defaultValue={0}
                            {...field}
                            onChange={(e) =>
                              field.onChange(Number(e.target.value))
                            }
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                )
              )}
            </div>
            <DialogFooter>
              <DialogClose asChild>
                <Button type="submit" variant="ghost">
                  Create
                </Button>
              </DialogClose>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
