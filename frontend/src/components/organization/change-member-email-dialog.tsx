"use client"

import {
  type FormEvent,
  type MouseEvent,
  useEffect,
  useRef,
  useState,
} from "react"
import { type OrgMemberRead, organizationChangeOrgMemberEmail } from "@/client"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
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
import { getApiErrorDetail, type TracecatApiError } from "@/lib/errors"
import { useMutation, useQueryClient } from "@/lib/query"

const FORM_ID = "change-member-email-form"
// Excludes display-name syntax such as "<a@b.com>", which the API would
// normalize to a bare address.
const EMAIL_PATTERN =
  /^[^\s@<>()[\],;:"]+@[^\s@<>()[\],;:"]+\.[^\s@<>()[\],;:"]+$/

function validateEmail(value: string, currentEmail: string): string | null {
  const email = value.trim()
  if (!email) {
    return "Enter an email address."
  }
  if (!EMAIL_PATTERN.test(email)) {
    return "Enter a valid email address."
  }
  if (email.toLowerCase() === currentEmail.toLowerCase()) {
    return "This is already the member's email."
  }
  return null
}

interface ChangeMemberEmailDialogProps {
  member: OrgMemberRead | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

/**
 * Lets an organization admin change a member's sign-in email.
 *
 * The new email takes effect immediately and signs the member out of every
 * session. Submitting the form swaps it for a confirmation alert dialog;
 * cancelling returns to the form.
 */
export function ChangeMemberEmailDialog({
  member,
  open,
  onOpenChange,
}: ChangeMemberEmailDialogProps) {
  const queryClient = useQueryClient()
  const [email, setEmail] = useState("")
  const [touched, setTouched] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (open) {
      setEmail("")
      setTouched(false)
      setConfirming(false)
    }
  }, [open])

  const { mutateAsync, isPending } = useMutation<
    unknown,
    TracecatApiError,
    { userId: string; email: string }
  >({
    mutationFn: async ({ userId, email }) =>
      await organizationChangeOrgMemberEmail({
        userId,
        requestBody: { email },
      }),
    onSuccess: async (_, { email }) => {
      await queryClient.invalidateQueries({ queryKey: ["org-members"] })
      toast({
        title: "Email changed",
        description: `${email} can now sign in. They were signed out of all sessions.`,
      })
      onOpenChange(false)
    },
    onError: (error) => {
      toast({
        title: "Failed to change email",
        description: getApiErrorDetail(error) ?? "Please try again.",
        variant: "destructive",
      })
      setConfirming(false)
    },
  })

  if (!member) {
    return null
  }
  const error = validateEmail(email, member.email)
  const showError = touched && error !== null

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setTouched(true)
    if (error !== null) {
      inputRef.current?.focus()
      return
    }
    if (member?.user_id) {
      setConfirming(true)
    }
  }

  async function handleConfirm(event: MouseEvent<HTMLButtonElement>) {
    event.preventDefault()
    if (!member?.user_id) {
      return
    }
    await mutateAsync({ userId: member.user_id, email: email.trim() })
  }

  return (
    <>
      <Dialog
        open={open && !confirming}
        onOpenChange={(next) => {
          if (!next) onOpenChange(false)
        }}
      >
        <DialogContent size="md">
          <DialogHeader>
            <DialogTitle>Change email</DialogTitle>
            <DialogDescription>
              Change the email {member.email} uses to sign in.
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            <form
              id={FORM_ID}
              noValidate
              onSubmit={handleSubmit}
              className="space-y-4"
            >
              <div className="space-y-2">
                <Label htmlFor="member-new-email">New email</Label>
                <Input
                  ref={inputRef}
                  id="member-new-email"
                  type="email"
                  autoComplete="off"
                  placeholder="name@example.com"
                  value={email}
                  disabled={isPending}
                  aria-invalid={showError}
                  aria-describedby={
                    showError ? "member-new-email-error" : undefined
                  }
                  onChange={(event) => setEmail(event.target.value)}
                  onBlur={() => setTouched(true)}
                />
                {showError && (
                  <p
                    id="member-new-email-error"
                    className="text-xs text-destructive"
                  >
                    {error}
                  </p>
                )}
              </div>
            </form>
          </DialogBody>
          <DialogFooter>
            <Button size="sm" type="submit" form={FORM_ID} disabled={isPending}>
              Change email and sign out
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <AlertDialog
        open={open && confirming}
        onOpenChange={(next) => {
          if (!isPending) setConfirming(next)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Change email and sign out?</AlertDialogTitle>
            <AlertDialogDescription>
              {member.email} will be signed out of all sessions and must sign in
              again with {email.trim()}. The new email isn&apos;t verified, so
              make sure it belongs to them.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isPending}>Cancel</AlertDialogCancel>
            <AlertDialogAction disabled={isPending} onClick={handleConfirm}>
              Change email
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
