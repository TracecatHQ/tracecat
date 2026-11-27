"use client"

import { type FormEvent, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useSignInCredentials } from "@/hooks/use-sign-in-credentials"

// The sign-in form rejects shorter passwords, so don't let users save one.
const MIN_PASSWORD_LENGTH = 12

/** Password change form for users who sign in with a password. */
export function SignInCredentials() {
  const { canChangeCredentials, isLoading } = useSignInCredentials()
  if (isLoading || !canChangeCredentials) {
    return null
  }
  return <ChangePasswordForm />
}

function ChangePasswordForm() {
  const { changePassword, changePasswordPending } = useSignInCredentials()
  const [currentPassword, setCurrentPassword] = useState("")
  const [newPassword, setNewPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const mismatch = confirmPassword.length > 0 && newPassword !== confirmPassword
  const tooShort =
    newPassword.length > 0 && newPassword.length < MIN_PASSWORD_LENGTH

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (newPassword !== confirmPassword || tooShort) {
      return
    }
    try {
      await changePassword({
        current_password: currentPassword,
        new_password: newPassword,
      })
      setCurrentPassword("")
      setNewPassword("")
      setConfirmPassword("")
    } catch {
      // Toast is shown by the mutation.
    }
  }

  return (
    <form className="space-y-3" onSubmit={onSubmit}>
      <div className="space-y-1">
        <h3 className="text-lg font-semibold">Password</h3>
        <p className="text-sm text-muted-foreground">
          Changing your password signs you out everywhere except this browser.
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="space-y-1.5">
          <Label htmlFor="change-password-current">Current password</Label>
          <Input
            id="change-password-current"
            type="password"
            autoComplete="current-password"
            required
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="change-password-new">New password</Label>
          <Input
            id="change-password-new"
            type="password"
            autoComplete="new-password"
            required
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="change-password-confirm">Confirm new password</Label>
          <Input
            id="change-password-confirm"
            type="password"
            autoComplete="new-password"
            required
            aria-invalid={mismatch}
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
          />
        </div>
      </div>
      {tooShort && (
        <p className="text-sm text-destructive">
          Use at least {MIN_PASSWORD_LENGTH} characters.
        </p>
      )}
      {mismatch && (
        <p className="text-sm text-destructive">Passwords don&apos;t match.</p>
      )}
      <Button
        type="submit"
        variant="outline"
        disabled={
          changePasswordPending ||
          !currentPassword ||
          !newPassword ||
          tooShort ||
          newPassword !== confirmPassword
        }
      >
        {changePasswordPending ? "Updating..." : "Change password"}
      </Button>
    </form>
  )
}
