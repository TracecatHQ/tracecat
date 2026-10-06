"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { PlusIcon } from "@radix-ui/react-icons"
import {
  ChevronDownIcon,
  FolderIcon,
  GlobeIcon,
  MinusIcon,
  TriangleAlertIcon,
} from "lucide-react"
import { useState } from "react"
import { useFieldArray, useForm } from "react-hook-form"
import { z } from "zod"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { CheckIndicator } from "@/components/ui/check-indicator"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
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
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useEntitlements } from "@/hooks/use-entitlements"
import {
  useOrgMembers,
  useRbacGroups,
  useRbacRoles,
  useWorkspaceManager,
} from "@/lib/hooks"
import { rolesForScope } from "@/lib/rbac"
import { cn } from "@/lib/utils"

/** Scope value standing in for an organization-wide grant. */
const ORG_WIDE = "org-wide"

const inviteFormSchema = z.object({
  email: z.string().email("Invalid email address"),
  group_ids: z.array(z.string()),
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
}

/**
 * Invite a user to the organization with one or more role grants, each scoped
 * organization-wide or to a single workspace.
 */
export function InviteMemberDialog({
  open,
  onOpenChange,
}: InviteMemberDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[min(85dvh,640px)] w-[calc(100%-2rem)] max-w-lg flex-col overflow-hidden">
        {/* Mounted only while open so its queries stay idle when closed. */}
        {open && <InviteMemberForm onOpenChange={onOpenChange} />}
      </DialogContent>
    </Dialog>
  )
}

/** Form body owning the invite queries; mounted only while the dialog is open. */
function InviteMemberForm({
  onOpenChange,
}: {
  onOpenChange: (open: boolean) => void
}) {
  const { createInvitation, createInvitationIsPending } = useOrgMembers()
  const [warning, setWarning] = useState<string | null>(null)
  const { roles } = useRbacRoles()
  const { workspaces } = useWorkspaceManager()
  const canUpdateRbac = useScopeCheck("org:rbac:update") === true
  const canReadRbac = useScopeCheck("org:rbac:read") === true
  const { hasEntitlement, hasEntitlementData } = useEntitlements()
  const canAssignGroups =
    canUpdateRbac &&
    canReadRbac &&
    hasEntitlementData &&
    hasEntitlement("rbac_addons")

  const form = useForm<InviteFormValues>({
    resolver: zodResolver(inviteFormSchema),
    defaultValues: {
      email: "",
      group_ids: [],
      grants: [{ scope: ORG_WIDE, role_id: "" }],
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
      const invitation = await createInvitation({
        email: values.email,
        grants: values.grants.map((grant) => ({
          role_id: grant.role_id,
          workspace_id: grant.scope === ORG_WIDE ? null : grant.scope,
        })),
        // Omitted when empty so invites without groups keep their shape.
        ...(values.group_ids.length > 0 && { group_ids: values.group_ids }),
      })
      form.reset()
      // Hold the dialog open so a SCIM advisory is read before it disappears.
      if (invitation.warning) {
        setWarning(invitation.warning)
        return
      }
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
        <form
          onSubmit={form.handleSubmit(handleSubmit)}
          className="flex min-h-0 flex-1 flex-col gap-4"
        >
          {/* Scrolling body; the right padding keeps the scrollbar off the rows. */}
          <div className="-ml-1 -mr-3 min-h-0 flex-1 space-y-4 overflow-y-auto pl-1 pr-3">
            {warning && (
              <Alert variant="warning">
                <TriangleAlertIcon className="size-4" />
                <AlertTitle>Invitation created</AlertTitle>
                <AlertDescription>{warning}</AlertDescription>
              </Alert>
            )}
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
            {canAssignGroups && (
              <FormField
                control={form.control}
                name="group_ids"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Groups</FormLabel>
                    <InviteGroupsPicker
                      value={field.value}
                      onChange={field.onChange}
                    />
                    <FormMessage />
                  </FormItem>
                )}
              />
            )}
            <div className="space-y-2">
              <Label>Role grants</Label>
              <div className="space-y-2">
                {fields.map((field, index) => {
                  const scope = grantValues?.[index]?.scope ?? ORG_WIDE
                  return (
                    <div
                      key={field.id}
                      className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]"
                    >
                      <FormField
                        control={form.control}
                        name={`grants.${index}.scope`}
                        render={({ field: scopeField }) => (
                          <FormItem className="col-start-1 min-w-0">
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
                                    scope !== ORG_WIDE &&
                                    usedScopes.has(ORG_WIDE)
                                  }
                                >
                                  <div className="flex min-w-0 items-center gap-2">
                                    <GlobeIcon className="size-4 shrink-0 text-blue-500" />
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
                          <FormItem className="col-start-1 row-start-2 min-w-0 sm:col-start-2 sm:row-start-1">
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
                                {rolesForScope(
                                  roles,
                                  scope === ORG_WIDE ? null : scope
                                ).map((role) => (
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
                        className="col-start-2 row-start-1 text-muted-foreground hover:text-foreground sm:col-start-3"
                      >
                        <MinusIcon className="size-4" />
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
 * Multi-select of the groups the invitee joins on acceptance. Owns the groups
 * query, so render it only for users allowed to assign groups.
 */
function InviteGroupsPicker({
  value,
  onChange,
}: {
  value: string[]
  onChange: (value: string[]) => void
}) {
  const [open, setOpen] = useState(false)
  const { groups } = useRbacGroups()
  // IdP-managed groups take their membership from the identity provider.
  const options = (groups ?? [])
    .filter((group) => !group.is_idp_managed)
    .sort((a, b) => a.name.localeCompare(b.name))
  const selected = options.filter((group) => value.includes(group.id))

  function toggle(groupId: string) {
    if (value.includes(groupId)) {
      onChange(value.filter((id) => id !== groupId))
      return
    }
    onChange([...value, groupId])
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <FormControl>
          <Button
            type="button"
            variant="outline"
            role="combobox"
            aria-expanded={open}
            className={cn(
              "h-auto min-h-9 w-full items-start justify-between whitespace-normal px-3 py-[7px] text-xs font-normal focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring",
              selected.length === 0 && "text-muted-foreground"
            )}
          >
            {selected.length === 0 ? (
              <span>Select groups</span>
            ) : (
              <span className="flex min-w-0 flex-wrap gap-1">
                {selected.map((group) => (
                  <span
                    key={group.id}
                    className="max-w-full truncate rounded-sm bg-secondary px-1.5 py-0.5 text-secondary-foreground"
                  >
                    {group.name}
                  </span>
                ))}
              </span>
            )}
            <span className="flex h-5 items-center">
              <ChevronDownIcon className="size-4 shrink-0 text-muted-foreground" />
            </span>
          </Button>
        </FormControl>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="w-[var(--radix-popover-trigger-width)] p-0"
      >
        <Command>
          <CommandInput placeholder="Search groups" className="text-sm" />
          <CommandList>
            <CommandEmpty>No groups found.</CommandEmpty>
            <CommandGroup>
              {options.map((group) => (
                <CommandItem
                  key={group.id}
                  value={group.id}
                  keywords={[group.name]}
                  onSelect={() => toggle(group.id)}
                  className="group"
                >
                  <CheckIndicator checked={value.includes(group.id)} />
                  <span className="truncate">{group.name}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}

/**
 * Button that opens the invite dialog, shown only to users who may invite.
 */
export function InviteMemberDialogButton() {
  const canInviteMembers = useScopeCheck("org:member:invite") === true
  const [open, setOpen] = useState(false)

  if (!canInviteMembers) {
    return null
  }

  return (
    <>
      <Button size="sm" onClick={() => setOpen(true)}>
        <PlusIcon className="mr-2 size-4" />
        Invite member
      </Button>
      <InviteMemberDialog open={open} onOpenChange={setOpen} />
    </>
  )
}
