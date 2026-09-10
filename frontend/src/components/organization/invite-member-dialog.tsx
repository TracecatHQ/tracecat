"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { PlusIcon } from "@radix-ui/react-icons"
import { FolderIcon, GlobeIcon, Trash2Icon } from "lucide-react"
import { useEffect, useState } from "react"
import { useFieldArray, useForm } from "react-hook-form"
import { z } from "zod"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useOrgMembers, useRbacRoles, useWorkspaceManager } from "@/lib/hooks"

/** Scope value standing in for an organization-wide grant. */
const ORG_WIDE = "org-wide"

const inviteFormSchema = z.object({
  email: z.string().email("Invalid email address"),
  grants: z
    .array(
      z.object({
        scope: z.string().min(1, "Select a scope"),
        role_id: z.string().uuid("Select a role"),
      })
    )
    .min(1, "Add at least one role grant")
    .superRefine((grants, ctx) => {
      const seen = new Set<string>()
      grants.forEach((grant, index) => {
        if (seen.has(grant.scope)) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            message: "This scope already has a grant",
            path: [index, "scope"],
          })
        }
        seen.add(grant.scope)
      })
    }),
})

type InviteFormValues = z.infer<typeof inviteFormSchema>

export interface InviteMemberDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Workspace to pre-fill as the first grant's scope, if any. */
  initialWorkspaceId?: string | null
}

/**
 * Invite a user to the organization with one or more role grants, each scoped
 * organization-wide or to a single workspace.
 */
export function InviteMemberDialog({
  open,
  onOpenChange,
  initialWorkspaceId,
}: InviteMemberDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        {/* Mounted only while open so its queries stay idle when closed. */}
        {open && (
          <InviteMemberForm
            onOpenChange={onOpenChange}
            initialWorkspaceId={initialWorkspaceId}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

/** Form body owning the invite queries; mounted only while the dialog is open. */
function InviteMemberForm({
  onOpenChange,
  initialWorkspaceId,
}: {
  onOpenChange: (open: boolean) => void
  initialWorkspaceId?: string | null
}) {
  const { createInvitation, createInvitationIsPending } = useOrgMembers()
  const { roles } = useRbacRoles()
  // Everyone gets organization-member on join; it is never something to grant.
  const grantableRoles = roles.filter((r) => r.slug !== "organization-member")
  const { workspaces } = useWorkspaceManager()

  const form = useForm<InviteFormValues>({
    resolver: zodResolver(inviteFormSchema),
    defaultValues: {
      email: "",
      grants: [{ scope: initialWorkspaceId ?? ORG_WIDE, role_id: "" }],
    },
  })
  const { fields, append, remove } = useFieldArray({
    control: form.control,
    name: "grants",
  })

  const grantValues = form.watch("grants")
  const usedScopes = new Set(grantValues?.map((grant) => grant.scope) ?? [])

  const handleSubmit = async (values: InviteFormValues) => {
    try {
      await createInvitation({
        email: values.email,
        grants: values.grants.map((grant) => ({
          role_id: grant.role_id,
          workspace_id: grant.scope === ORG_WIDE ? null : grant.scope,
        })),
      })
      form.reset()
      onOpenChange(false)
    } catch {
      // Error handled in hook
    }
  }

  const availableScopeCount = 1 + (workspaces?.length ?? 0)
  const canAddGrant = fields.length < availableScopeCount

  return (
    <>
      <DialogHeader>
        <DialogTitle>Invite member</DialogTitle>
        <DialogDescription>
          Send an invitation to join this organization. Each grant assigns a
          role organization-wide or on a single workspace.
        </DialogDescription>
      </DialogHeader>
      <Form {...form}>
        <form onSubmit={form.handleSubmit(handleSubmit)} className="space-y-4">
          <FormField
            control={form.control}
            name="email"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Email</FormLabel>
                <FormControl>
                  <Input
                    placeholder="user@example.com"
                    type="email"
                    {...field}
                  />
                </FormControl>
                <FormDescription>
                  The email address of the person to invite.
                </FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
          <div className="space-y-2">
            <Label>Role grants</Label>
            <div className="space-y-2">
              {fields.map((field, index) => {
                const scope = grantValues?.[index]?.scope ?? ORG_WIDE
                return (
                  <div key={field.id} className="flex items-start gap-2">
                    <FormField
                      control={form.control}
                      name={`grants.${index}.scope`}
                      render={({ field: scopeField }) => (
                        <FormItem className="w-[180px]">
                          <Select
                            value={scopeField.value}
                            onValueChange={(value) => {
                              scopeField.onChange(value)
                              // Roles differ per scope, so drop a stale pick.
                              form.setValue(`grants.${index}.role_id`, "")
                            }}
                          >
                            <FormControl>
                              <SelectTrigger
                                aria-label={`Grant ${index + 1} scope`}
                              >
                                <SelectValue placeholder="Scope" />
                              </SelectTrigger>
                            </FormControl>
                            <SelectContent>
                              <SelectItem
                                value={ORG_WIDE}
                                disabled={
                                  scope !== ORG_WIDE && usedScopes.has(ORG_WIDE)
                                }
                              >
                                <div className="flex items-center gap-2">
                                  <GlobeIcon className="size-4 text-blue-500" />
                                  Organization-wide
                                </div>
                              </SelectItem>
                              {workspaces?.map((workspace) => (
                                <SelectItem
                                  key={workspace.id}
                                  value={workspace.id}
                                  disabled={
                                    scope !== workspace.id &&
                                    usedScopes.has(workspace.id)
                                  }
                                >
                                  <div className="flex items-center gap-2">
                                    <FolderIcon className="size-4 text-muted-foreground" />
                                    {workspace.name}
                                  </div>
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name={`grants.${index}.role_id`}
                      render={({ field: roleField }) => (
                        <FormItem className="flex-1">
                          <Select
                            value={roleField.value}
                            onValueChange={roleField.onChange}
                          >
                            <FormControl>
                              <SelectTrigger
                                aria-label={`Grant ${index + 1} role`}
                              >
                                <SelectValue placeholder="Select a role" />
                              </SelectTrigger>
                            </FormControl>
                            <SelectContent>
                              {grantableRoles.map((role) => (
                                <SelectItem key={role.id} value={role.id}>
                                  {role.name}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      aria-label={`Remove grant ${index + 1}`}
                      disabled={fields.length === 1}
                      onClick={() => remove(index)}
                      className="text-rose-500 hover:text-rose-600"
                    >
                      <Trash2Icon className="size-4" />
                    </Button>
                  </div>
                )
              })}
            </div>
            {form.formState.errors.grants?.root && (
              <p className="text-sm font-medium text-destructive">
                {form.formState.errors.grants.root.message}
              </p>
            )}
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!canAddGrant}
              onClick={() => append({ scope: "", role_id: "" })}
            >
              <PlusIcon className="mr-2 size-4" />
              Add grant
            </Button>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={createInvitationIsPending}>
              {createInvitationIsPending ? "Sending..." : "Send invitation"}
            </Button>
          </DialogFooter>
        </form>
      </Form>
    </>
  )
}

/**
 * Button that opens the invite dialog, shown only to users who may invite.
 * Reads the `inviteWorkspace` query parameter to pre-fill the first grant.
 */
export function InviteMemberDialogButton({
  initialWorkspaceId,
}: {
  initialWorkspaceId?: string | null
}) {
  const canInviteMembers = useScopeCheck("org:member:invite") === true
  const [open, setOpen] = useState(false)

  // Open automatically when a workspace handoff supplied the parameter.
  useEffect(() => {
    if (initialWorkspaceId) {
      setOpen(true)
    }
  }, [initialWorkspaceId])

  if (!canInviteMembers) {
    return null
  }

  return (
    <>
      <Button size="sm" onClick={() => setOpen(true)}>
        <PlusIcon className="mr-2 size-4" />
        Invite member
      </Button>
      <InviteMemberDialog
        open={open}
        onOpenChange={setOpen}
        initialWorkspaceId={initialWorkspaceId}
      />
    </>
  )
}
