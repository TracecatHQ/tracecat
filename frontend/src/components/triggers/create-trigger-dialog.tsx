"use client"

import { ChevronLeftIcon, ChevronRightIcon, PlusIcon } from "lucide-react"
import { useRouter } from "next/navigation"
import { useState } from "react"
import type { TriggerKind } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import type { TriggerConfig } from "@/components/triggers/trigger-kind-types"
import {
  TRIGGER_KIND_LIST,
  TRIGGER_KINDS,
} from "@/components/triggers/trigger-kinds"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { toast } from "@/components/ui/use-toast"
import { useTriggerMutations } from "@/hooks/use-triggers"
import { useTriggersAccess } from "@/hooks/use-triggers-access"
import { useWorkspaceId } from "@/providers/workspace-id"

const CREATE_TRIGGER_FORM_ID = "create-trigger-form"

/** The Triggers page header action. Hidden without `trigger:create`. */
export function CreateTriggerButton() {
  const canCreate = useScopeCheck("trigger:create")
  const access = useTriggersAccess()
  const [open, setOpen] = useState(false)

  if (access !== "enabled" || canCreate !== true) {
    return null
  }

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        className="h-7 bg-background"
        onClick={() => setOpen(true)}
      >
        <PlusIcon className="mr-1 h-3.5 w-3.5" />
        Create trigger
      </Button>
      {open ? <CreateTriggerDialog open={open} onOpenChange={setOpen} /> : null}
    </>
  )
}

/**
 * Create a trigger: pick its kind, then fill in the name and that kind's
 * settings form from `TRIGGER_KINDS`. Opens the new trigger on success.
 */
export function CreateTriggerDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const workspaceId = useWorkspaceId()
  const router = useRouter()
  const { createTrigger } = useTriggerMutations(workspaceId)
  const [kind, setKind] = useState<TriggerKind | null>(null)
  const [name, setName] = useState("")

  const definition = kind ? TRIGGER_KINDS[kind] : null

  async function handleCreate(config: TriggerConfig) {
    const trigger = await createTrigger.mutateAsync({
      name: name.trim(),
      config,
    })
    onOpenChange(false)
    toast({
      title: "Trigger created",
      description: "Add a target, then put the trigger online.",
    })
    router.push(`/workspaces/${workspaceId}/triggers/${trigger.id}`)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>
            {definition
              ? `New ${definition.label.toLowerCase()} trigger`
              : "Create trigger"}
          </DialogTitle>
          <DialogDescription>
            {definition
              ? definition.description
              : "Pick what kind of event starts your workflows and agents."}
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          {definition ? (
            <div className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="trigger-name" className="text-xs">
                  Name
                </Label>
                <Input
                  id="trigger-name"
                  form={CREATE_TRIGGER_FORM_ID}
                  className="text-xs"
                  placeholder="New uploads"
                  value={name}
                  required
                  maxLength={255}
                  disabled={createTrigger.isPending}
                  onChange={(event) => setName(event.target.value)}
                />
              </div>
              <definition.CreateForm
                formId={CREATE_TRIGGER_FORM_ID}
                pending={createTrigger.isPending}
                onSubmit={handleCreate}
              />
            </div>
          ) : (
            <div className="space-y-2">
              {TRIGGER_KIND_LIST.map((option) => (
                <button
                  key={option.kind}
                  type="button"
                  className="flex w-full items-center gap-3 rounded-md border px-3 py-3 text-left transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
                  onClick={() => setKind(option.kind)}
                >
                  <option.icon className="size-4 shrink-0 text-muted-foreground" />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium">{option.label}</p>
                    <p className="text-xs text-muted-foreground">
                      {option.description}
                    </p>
                  </div>
                  <ChevronRightIcon className="size-4 shrink-0 text-muted-foreground" />
                </button>
              ))}
            </div>
          )}
        </DialogBody>
        {definition ? (
          <DialogFooter className="sm:justify-between">
            <Button
              size="sm"
              variant="ghost"
              type="button"
              onClick={() => setKind(null)}
              disabled={createTrigger.isPending}
            >
              <ChevronLeftIcon className="mr-1 size-3.5" />
              Kind
            </Button>
            <Button
              size="sm"
              type="submit"
              form={CREATE_TRIGGER_FORM_ID}
              disabled={createTrigger.isPending}
            >
              Create trigger
            </Button>
          </DialogFooter>
        ) : null}
      </DialogContent>
    </Dialog>
  )
}
