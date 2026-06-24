"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { DialogTrigger } from "@radix-ui/react-dialog"
import { PlusIcon } from "@radix-ui/react-icons"
import { type ReactNode, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { parseEmailList } from "@/lib/email-parse"
import { toast } from "../ui/use-toast"

const bulkInviteFormSchema = z.object({
  emails: z.string().min(1, "Enter at least one email address"),
  role_id: z.string().uuid("Please select a role"),
})

type BulkInviteFormValues = z.infer<typeof bulkInviteFormSchema>

interface RoleOption {
  id: string
  name: string
}

type BulkInviteResultRow = {
  email: string
  status: string
  reason?: string | null
}

interface BulkInviteResult {
  results: BulkInviteResultRow[]
  created_count: number
  skipped_count: number
}

interface BulkInviteDialogProps {
  /** Title shown in the dialog header. */
  title: string
  /** Description shown beneath the title. */
  description: string
  /** Roles selectable for the invited members. */
  roles: RoleOption[]
  /** Whether email delivery is configured (affects helper copy). */
  emailConfigured: boolean
  /** Submit handler — issues the bulk invitations. */
  onSubmit: (params: {
    emails: string[]
    role_id: string
  }) => Promise<BulkInviteResult>
  /** Whether the submit mutation is in flight. */
  isPending: boolean
  /** Optional custom trigger; defaults to an "Invite member" button. */
  trigger?: ReactNode
}

/**
 * Dialog for inviting multiple members by email. Parses a textarea of
 * comma/space/newline separated emails, submits them in bulk, and reports the
 * per-email outcome. Shared between organization and workspace invite flows.
 */
export function BulkInviteDialog({
  title,
  description,
  roles,
  emailConfigured,
  onSubmit,
  isPending,
  trigger,
}: BulkInviteDialogProps) {
  const [open, setOpen] = useState(false)
  const [invalidEmails, setInvalidEmails] = useState<string[]>([])
  const [skipped, setSkipped] = useState<BulkInviteResultRow[]>([])

  const form = useForm<BulkInviteFormValues>({
    resolver: zodResolver(bulkInviteFormSchema),
    defaultValues: { emails: "", role_id: "" },
  })

  function handleOpenChange(next: boolean) {
    setOpen(next)
    if (!next) {
      form.reset()
      setInvalidEmails([])
      setSkipped([])
    }
  }

  const handleSubmit = async (values: BulkInviteFormValues) => {
    const { valid, invalid } = parseEmailList(values.emails)
    setInvalidEmails(invalid)

    if (valid.length === 0) {
      form.setError("emails", {
        message: "No valid email addresses found",
      })
      return
    }

    try {
      const result = await onSubmit({ emails: valid, role_id: values.role_id })

      const count = result.created_count
      const noun = count === 1 ? "invitation" : "invitations"
      toast({
        title: "Invitations sent",
        description:
          count > 0
            ? `${count} ${noun} ${emailConfigured ? "sent" : "created"} successfully.`
            : "No new invitations were created.",
      })

      // Keep the dialog open when some emails were skipped so the admin can see
      // why; otherwise reset and close on a clean success.
      const skippedRows = result.results.filter((r) => r.status !== "created")
      setSkipped(skippedRows)
      if (skippedRows.length === 0) {
        handleOpenChange(false)
      } else {
        form.reset()
        setInvalidEmails([])
      }
    } catch {
      // Error handled by the mutation hook.
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        {trigger ?? (
          <Button size="sm">
            <PlusIcon className="mr-2 size-4" />
            Invite member
          </Button>
        )}
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(handleSubmit)}
            className="space-y-4"
          >
            <FormField
              control={form.control}
              name="emails"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Emails</FormLabel>
                  <FormControl>
                    <Textarea
                      placeholder="Enter emails separated by commas, spaces, or new lines"
                      rows={4}
                      {...field}
                    />
                  </FormControl>
                  <FormDescription>
                    {emailConfigured
                      ? "Each person will receive an invitation email."
                      : "Share the invitation links after creating them."}
                  </FormDescription>
                  <FormMessage />
                  {invalidEmails.length > 0 && (
                    <p className="text-sm text-rose-500">
                      Ignored invalid: {invalidEmails.join(", ")}
                    </p>
                  )}
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="role_id"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Role</FormLabel>
                  <Select
                    onValueChange={field.onChange}
                    defaultValue={field.value}
                  >
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue placeholder="Select a role" />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {roles.map((role) => (
                        <SelectItem key={role.id} value={role.id}>
                          {role.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormDescription>
                    The role to assign when an invitation is accepted.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            {skipped.length > 0 && (
              <div className="space-y-1 rounded-md border border-border p-3">
                <p className="text-sm font-medium">{skipped.length} skipped</p>
                <ul className="space-y-0.5 text-sm text-muted-foreground">
                  {skipped.map((row) => (
                    <li key={row.email}>
                      <span className="font-medium text-foreground">
                        {row.email}
                      </span>
                      {row.reason ? `: ${row.reason}` : ` (${row.status})`}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => handleOpenChange(false)}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={isPending}>
                {isPending ? "Sending..." : "Send invitations"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
