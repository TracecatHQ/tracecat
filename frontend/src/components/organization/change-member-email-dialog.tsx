"use client"

import { type FormEvent, useEffect, useState } from "react"
import { type OrgMemberRead, organizationChangeOrgMemberEmail } from "@/client"
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
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

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
 * session, so the admin confirms before saving.
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

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setTouched(true)
    if (error !== null || !member?.user_id) {
      return
    }
    if (!confirming) {
      setConfirming(true)
      return
    }
    await mutateAsync({ userId: member.user_id, email: email.trim() })
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!isPending) onOpenChange(next)
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
          <form id={FORM_ID} onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="member-new-email">New email</Label>
              <Input
                id="member-new-email"
                type="email"
                autoComplete="off"
                placeholder="name@example.com"
                value={email}
                disabled={confirming || isPending}
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
            {confirming && (
              <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
                {member.email} will be signed out of all sessions and must sign
                in again with {email.trim()}. The new address isn&apos;t
                verified, so make sure it belongs to them.
              </div>
            )}
          </form>
        </DialogBody>
        <DialogFooter>
          {confirming && (
            <Button
              size="sm"
              variant="outline"
              type="button"
              disabled={isPending}
              onClick={() => setConfirming(false)}
            >
              Back
            </Button>
          )}
          <Button size="sm" type="submit" form={FORM_ID} disabled={isPending}>
            {confirming ? "Change email and sign out" : "Continue"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
