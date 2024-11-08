"use client"

import "react18-json-view/src/style.css"

import React from "react"
import { ApiError, WebhookResponse, WorkflowResponse } from "@/client"
import { useWorkspace } from "@/providers/workspace"
import { zodResolver } from "@hookform/resolvers/zod"
import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import {
  BanIcon,
  CalendarClockIcon,
  PlusCircleIcon,
  SettingsIcon,
  WebhookIcon,
} from "lucide-react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { useSchedules, useUpdateWebhook } from "@/lib/hooks"
import {
  durationSchema,
  durationToHumanReadable,
  durationToISOString,
} from "@/lib/time"
import { cn } from "@/lib/utils"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
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
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
import { Switch } from "@/components/ui/switch"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"


import { toast } from "@/components/ui/use-toast"
import { CopyButton } from "@/components/copy-button"
import { CustomEditor } from "@/components/editor"
import { getIcon } from "@/components/icons"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import {
  TriggerNodeData,
  TriggerTypename,
} from "@/components/workbench/canvas/trigger-node"

export function TriggerPanel({
  workflow,
}: {
  nodeData: TriggerNodeData
  workflow: WorkflowResponse
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
              <ScheduleControls workflowId={workflow.id} />
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
  webhook: WebhookResponse
  workflowId: string
}) {
  const { workspaceId } = useWorkspace()
  const { mutateAsync } = useUpdateWebhook(workspaceId, workflowId)
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
export function ScheduleControls({ workflowId }: { workflowId: string }) {
  const {
    schedules,
    schedulesIsLoading,
    schedulesError,
    updateSchedule,
    deleteSchedule,
  } = useSchedules(workflowId)
  const { workspaceId } = useWorkspace()

  if (schedulesIsLoading) {
    return <CenteredSpinner />
  }
  if (schedulesError || !schedules) {
    return (
      <AlertNotification
        title="Failed to load schedules"
        message="There was an error when loading schedules."
      />
    )
  }

  return (
    <div className="rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="pl-3 text-xs font-semibold">
              Schedule ID
            </TableHead>
            <TableHead className="text-xs font-semibold">Interval</TableHead>
            <TableHead className="text-xs font-semibold">Status</TableHead>
            <TableHead className="pr-3 text-right text-xs font-semibold">
              Actions
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {schedules.length > 0 ? (
            schedules.map(({ id, status, inputs, every }) => (
              <HoverCard>
                <HoverCardTrigger asChild className="hover:border-none">
                  <TableRow key={id} className="ext-xs text-muted-foreground">
                    <TableCell className="items-center pl-3 text-xs">
                      {id}
                    </TableCell>
                    <TableCell className="items-center text-xs">
                      {durationToHumanReadable(every)}
                    </TableCell>
                    <TableCell className="text-xs capitalize">
                      <div className="flex">
                        <p>{status}</p>
                      </div>
                    </TableCell>
                    <TableCell className="items-center pr-3 text-xs">
                      <div className="flex justify-end">
                        <AlertDialog>
                          <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                              <Button className="size-6 p-0" variant="ghost">
                                <DotsHorizontalIcon className="size-4" />
                              </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end">
                              <DropdownMenuLabel className="text-xs">
                                Actions
                              </DropdownMenuLabel>
                              <DropdownMenuItem
                                onClick={() =>
                                  navigator.clipboard.writeText(id!)
                                }
                                className="text-xs"
                              >
                                Copy ID
                              </DropdownMenuItem>
                              <DropdownMenuSeparator />
                              <DropdownMenuItem
                                className={cn("text-xs", status === "online")}
                                onClick={async () =>
                                  await updateSchedule({
                                    workspaceId,
                                    scheduleId: id!,
                                    requestBody: {
                                      status:
                                        status === "online"
                                          ? "offline"
                                          : "online",
                                    },
                                  })
                                }
                              >
                                {status === "online" ? "Pause" : "Unpause"}
                              </DropdownMenuItem>
                              <AlertDialogTrigger asChild>
                                <DropdownMenuItem className="text-xs text-rose-500 focus:text-rose-600">
                                  Delete
                                </DropdownMenuItem>
                              </AlertDialogTrigger>
                            </DropdownMenuContent>
                          </DropdownMenu>
                          <AlertDialogContent>
                            <AlertDialogHeader>
                              <AlertDialogTitle>Are you sure?</AlertDialogTitle>
                              <AlertDialogDescription>
                                Are you sure you want to delete this schedule?
                                This action cannot be undone.
                              </AlertDialogDescription>
                            </AlertDialogHeader>
                            <AlertDialogFooter>
                              <AlertDialogCancel>Cancel</AlertDialogCancel>
                              <AlertDialogAction
                                variant="destructive"
                                onClick={async () =>
                                  await deleteSchedule({
                                    workspaceId,
                                    scheduleId: id!,
                                  })
                                }
                              >
                                Confirm
                              </AlertDialogAction>
                            </AlertDialogFooter>
                          </AlertDialogContent>
                        </AlertDialog>
                      </div>
                    </TableCell>
                  </TableRow>
                </HoverCardTrigger>
                <HoverCardContent
                  className="max-w-300 w-200 space-y-2 p-3"
                  side="left"
                  align="start"
                >
                  <div className="w-full space-y-1">
                    <span className="text-xs font-semibold text-muted-foreground">
                      Inputs
                    </span>
                    <div className="rounded-md border bg-muted-foreground/10 p-2">
                      <pre className="text-xs font-light text-foreground/80">
                        {JSON.stringify(inputs, null, 2)}
                      </pre>
                    </div>
                  </div>
                </HoverCardContent>
              </HoverCard>
            ))
          ) : (
            <TableRow className="justify-center text-xs text-muted-foreground">
              <TableCell
                className="h-8 bg-muted-foreground/5 text-center"
                colSpan={4}
              >
                No Schedules
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
      <Separator />
      <CreateScheduleDialog workflowId={workflowId} />
    </div>
  )
}

const scheduleInputsSchema = z.object({
  duration: durationSchema,
  inputs: z
    .string()
    .optional()
    .refine((val) => {
      if (!val) return true
      try {
        JSON.parse(val)
        return true
      } catch {
        return false
      }
    }, "Invalid JSON format")
    .transform((val) => (val ? JSON.parse(val) : {})),
})
type DurationType =
  | "duration.years"
  | "duration.months"
  | "duration.days"
  | "duration.hours"
  | "duration.minutes"
  | "duration.seconds"
type ScheduleInputs = z.infer<typeof scheduleInputsSchema>

export function CreateScheduleDialog({ workflowId }: { workflowId: string }) {
  const { createSchedule } = useSchedules(workflowId)
  const { workspaceId } = useWorkspace()
  const form = useForm<ScheduleInputs>({
    resolver: zodResolver(scheduleInputsSchema),
    defaultValues: {
      inputs: '{"sampleWebhookParam": "sampleValue"}',
    },
  })

  const onSubmit = async (values: ScheduleInputs) => {
    const { duration, inputs } = values
    try {
      const response = await createSchedule({
        workspaceId,
        requestBody: {
          workflow_id: workflowId,
          every: durationToISOString(duration),
          inputs,
        },
      })
      console.log("Schedule created", response)
    } catch (error) {
      if (error instanceof ApiError) {
        console.error("Failed to create schedule", error.body)
      } else {
        console.error("Unexpected error when creating schedule", error)
      }
    }
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
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create a new schedule</DialogTitle>
          <DialogDescription>
            Configure the schedule for the workflow. The workflow will not run
            immediately.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(onSubmit, () => {
              console.error("Form validation failed")
              toast({
                title: "Invalid inputs in form",
                description: "Please check the form for errors.",
              })
            })}
          >
            <div className="grid grid-cols-2 gap-2">
              {[
                "duration.years",
                "duration.months",
                "duration.days",
                "duration.hours",
                "duration.minutes",
                "duration.seconds",
              ].map((unit) => (
                <FormField
                  key={unit}
                  control={form.control}
                  name={unit as DurationType}
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
                          value={Math.max(0, Number(field.value || 0))}
                          {...form.register(unit as DurationType, {
                            valueAsNumber: true,
                          })}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              ))}

              <div className="col-span-2 w-full">
                <FormField
                  key="inputs"
                  control={form.control}
                  name="inputs"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-xs text-foreground/80">
                        <span>
                          Scheduled workflow inputs. Access these through the{" "}
                          <p className="inline-block rounded-sm bg-amber-100 font-mono">
                            TRIGGER
                          </p>{" "}
                          context.
                        </span>
                      </FormLabel>
                      <FormControl>
                        <CustomEditor
                          className="h-40 w-full"
                          defaultLanguage="yaml"
                          value={field.value}
                          onChange={field.onChange}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>
            </div>
            <DialogFooter className="mt-4">
              <DialogClose asChild>
                <Button type="submit" variant="default">
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
