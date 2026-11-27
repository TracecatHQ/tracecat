"use client"

import { ChevronLeftIcon, ChevronRightIcon, PlusIcon } from "lucide-react"
import { useRouter } from "next/navigation"
import { useRef, useState } from "react"
import type { TriggerKind, TriggerRead } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import type { TriggerConfig } from "@/components/triggers/trigger-kind-types"
import {
  getTriggerKind,
  TRIGGER_KIND_LIST,
  TRIGGER_KINDS,
} from "@/components/triggers/trigger-kinds"
import { useHasScopes } from "@/components/triggers/trigger-target-scopes"
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
import {
  type TriggerKindAccess,
  useTriggerKindAccess,
} from "@/hooks/use-triggers-access"
import { useWorkspaceId } from "@/providers/workspace-id"

const CREATE_TRIGGER_FORM_ID = "create-trigger-form"

// What the kind picker says beside a kind the org can't use yet.
const KIND_ACCESS_NOTE: Record<TriggerKindAccess, string | null> = {
  enabled: null,
  loading: "Checking plan…",
  unknown: "Plan unknown",
  locked: "Upgrade required",
}

/** The Triggers page header action. Hidden without `trigger:create`. */
export function CreateTriggerButton() {
  const canCreate = useScopeCheck("trigger:create")
  const [open, setOpen] = useState(false)

  if (canCreate !== true) {
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
  const kindAccess = useTriggerKindAccess()
  const hasScopes = useHasScopes()
  const [kind, setKind] = useState<TriggerKind | null>(null)
  const [name, setName] = useState("")
  const [submitting, setSubmitting] = useState(false)
  // State lags a fast double submit by a render; the ref doesn't.
  const submittingRef = useRef(false)

  const definition = kind ? TRIGGER_KINDS[kind] : null

  async function handleCreate(resolveConfig: () => Promise<TriggerConfig>) {
    if (submittingRef.current) return
    submittingRef.current = true
    setSubmitting(true)
    let trigger: TriggerRead
    try {
      // Read the name now: the field is disabled from here on.
      const triggerName = name.trim()
      // Check before the kind's own requests: a new source would be left
      // behind by a trigger the API then refuses.
      if (!triggerName) {
        throw new Error("Give the trigger a name.")
      }
      const config = await resolveConfig()
      trigger = await createTrigger.mutateAsync({ name: triggerName, config })
    } finally {
      submittingRef.current = false
      setSubmitting(false)
    }
    onOpenChange(false)
    toast({
      title: "Trigger created",
      description: getTriggerKind(trigger)?.createdHint,
    })
    router.push(`/workspaces/${workspaceId}/triggers/${trigger.id}`)
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        // Closing mid-submit would drop the error or navigate unasked.
        if (!next && submittingRef.current) return
        onOpenChange(next)
      }}
    >
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
                  placeholder={definition.namePlaceholder}
                  value={name}
                  required
                  maxLength={255}
                  disabled={submitting}
                  onChange={(event) => setName(event.target.value)}
                />
              </div>
              <definition.CreateForm
                formId={CREATE_TRIGGER_FORM_ID}
                pending={submitting}
                onSubmit={handleCreate}
              />
            </div>
          ) : (
            <div className="space-y-2">
              {TRIGGER_KIND_LIST.map((option) => {
                const access = kindAccess(option.entitlement)
                const permitted = hasScopes(option.scopes.create) === true
                const note =
                  KIND_ACCESS_NOTE[access] ??
                  (permitted ? null : "No permission")
                return (
                  <button
                    key={option.kind}
                    type="button"
                    disabled={access !== "enabled" || !permitted}
                    title={
                      permitted
                        ? undefined
                        : `Needs ${option.scopes.create.join(", ")}`
                    }
                    className="flex w-full items-center gap-3 rounded-md border px-3 py-3 text-left transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:bg-transparent"
                    onClick={() => setKind(option.kind)}
                  >
                    <option.icon className="size-4 shrink-0 text-muted-foreground" />
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium">{option.label}</p>
                      <p className="text-xs text-muted-foreground">
                        {option.description}
                      </p>
                    </div>
                    {note ? (
                      <span className="shrink-0 text-xs text-muted-foreground">
                        {note}
                      </span>
                    ) : (
                      <ChevronRightIcon className="size-4 shrink-0 text-muted-foreground" />
                    )}
                  </button>
                )
              })}
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
              disabled={submitting}
            >
              <ChevronLeftIcon className="mr-1 size-3.5" />
              Kind
            </Button>
            <Button
              size="sm"
              type="submit"
              form={CREATE_TRIGGER_FORM_ID}
              disabled={submitting}
            >
              Create trigger
            </Button>
          </DialogFooter>
        ) : null}
      </DialogContent>
    </Dialog>
  )
}
