"use client"

import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { toast } from "@/components/ui/use-toast"
import {
  useAdminOrganization,
  useAdminOrgEncryptedSettingReset,
} from "@/hooks/use-admin"

interface AdminOrgEncryptedSettingResetDialogProps {
  orgId: string
  trigger?: React.ReactNode
  open?: boolean
  onOpenChange?: (open: boolean) => void
}

interface ApiErrorLike {
  body?: {
    detail?: unknown
  }
}

const SUGGESTED_ENCRYPTED_KEYS = [
  "audit_webhook_url",
  "audit_webhook_custom_headers",
  "saml_idp_metadata_url",
]

function getErrorDetail(error: unknown, fallback: string): string {
  if (typeof error === "object" && error !== null) {
    const apiError = error as ApiErrorLike
    if (typeof apiError.body?.detail === "string") {
      return apiError.body.detail
    }
  }
  return fallback
}

export function AdminOrgEncryptedSettingResetDialog({
  orgId,
  trigger,
  open: controlledOpen,
  onOpenChange,
}: AdminOrgEncryptedSettingResetDialogProps) {
  const [internalOpen, setInternalOpen] = useState(false)
  const [settingKey, setSettingKey] = useState("audit_webhook_url")
  const [jsonValue, setJsonValue] = useState('"https://example.com/webhook"')

  const isControlled = controlledOpen !== undefined
  const dialogOpen = isControlled ? controlledOpen : internalOpen
  const setDialogOpen = (nextOpen: boolean) => {
    if (!isControlled) {
      setInternalOpen(nextOpen)
    }
    onOpenChange?.(nextOpen)
  }

  const { organization } = useAdminOrganization(orgId)
  const { resetEncryptedSetting, resetPending } =
    useAdminOrgEncryptedSettingReset(orgId)

  useEffect(() => {
    if (!dialogOpen) {
      setSettingKey("audit_webhook_url")
      setJsonValue('"https://example.com/webhook"')
    }
  }, [dialogOpen])

  const handleReset = async () => {
    const key = settingKey.trim()
    if (!key) {
      toast({
        title: "Missing setting key",
        description: "Enter the setting key to update.",
        variant: "destructive",
      })
      return
    }

    let parsedValue: unknown
    try {
      parsedValue = JSON.parse(jsonValue)
    } catch {
      toast({
        title: "Invalid JSON value",
        description:
          "Enter a valid JSON value. For strings, wrap the value in double quotes.",
        variant: "destructive",
      })
      return
    }

    try {
      await resetEncryptedSetting({ key, value: parsedValue })
      toast({
        title: "Setting updated",
        description: `${key} was overwritten and re-encrypted with the current key.`,
      })
      setDialogOpen(false)
    } catch (error) {
      toast({
        title: "Failed to update setting",
        description: getErrorDetail(error, "Please try again."),
        variant: "destructive",
      })
    }
  }

  return (
    <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
      {trigger ? <DialogTrigger asChild>{trigger}</DialogTrigger> : null}
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Manage settings</DialogTitle>
          <DialogDescription>
            Override an encrypted organization setting for{" "}
            {organization?.name ?? "organization"}. This writes a new value
            encrypted with the current key.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-2">
            <p className="text-sm font-medium">Setting key</p>
            <Input
              value={settingKey}
              onChange={(event) => setSettingKey(event.target.value)}
              placeholder="audit_webhook_url"
              disabled={resetPending}
            />
            <div className="flex flex-wrap gap-2">
              {SUGGESTED_ENCRYPTED_KEYS.map((key) => (
                <Button
                  key={key}
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => setSettingKey(key)}
                  disabled={resetPending}
                >
                  {key}
                </Button>
              ))}
            </div>
          </div>

          <div className="space-y-2">
            <p className="text-sm font-medium">JSON value</p>
            <Textarea
              value={jsonValue}
              onChange={(event) => setJsonValue(event.target.value)}
              className="min-h-36 font-mono"
              disabled={resetPending}
            />
            <p className="text-xs text-muted-foreground">
              Use valid JSON. Example string:{" "}
              <span className="font-mono">&quot;https://example.com&quot;</span>
              .
            </p>
          </div>
        </div>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => setDialogOpen(false)}
            disabled={resetPending}
          >
            Cancel
          </Button>
          <Button type="button" onClick={handleReset} disabled={resetPending}>
            {resetPending ? "Saving..." : "Save setting"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
