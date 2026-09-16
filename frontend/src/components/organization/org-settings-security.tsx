"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import {
  CheckCircle2Icon,
  PencilIcon,
  PlusIcon,
  ShieldAlertIcon,
  Trash2Icon,
  XCircleIcon,
} from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { IPAllowlist, IPAllowlistCheckResult } from "@/client"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
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
import { Switch } from "@/components/ui/switch"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Textarea } from "@/components/ui/textarea"
import { useOrgSecuritySettings } from "@/hooks/use-org-security-settings"

const IPV4_CIDR_PATTERN =
  /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}(\/(3[0-2]|[12]?\d))?$/
const IPV6_CIDR_PATTERN = /^[0-9a-fA-F:]+(\/(12[0-8]|1[01]\d|[1-9]?\d))?$/
const MAX_ALLOWLISTS = 100
const MAX_CIDRS_PER_ALLOWLIST = 50

function isValidCidr(value: string): boolean {
  if (IPV4_CIDR_PATTERN.test(value)) {
    return true
  }
  return value.includes(":") && IPV6_CIDR_PATTERN.test(value)
}

function parseCidrLines(text: string): string[] {
  return text
    .split(/[\n,]/)
    .map((line) => line.trim())
    .filter((line) => line !== "")
}

const allowlistFormSchema = z.object({
  name: z.string().trim().min(1, "Name is required").max(100),
  description: z.string().trim().max(500).optional(),
  cidrs: z
    .string()
    .refine((text) => parseCidrLines(text).length > 0, {
      message: "Add at least one IP address or CIDR range",
    })
    .refine((text) => parseCidrLines(text).length <= MAX_CIDRS_PER_ALLOWLIST, {
      message: `At most ${MAX_CIDRS_PER_ALLOWLIST} ranges per allowlist`,
    })
    .refine((text) => parseCidrLines(text).every(isValidCidr), {
      message: "Every line must be a valid IPv4/IPv6 address or CIDR range",
    }),
})

type AllowlistFormValues = z.infer<typeof allowlistFormSchema>

/**
 * Organization IP allowlist settings: enforcement toggle, named allowlists
 * managed through a create/edit dialog, and a saved-policy IP checker.
 */
export function OrgSettingsSecurityForm() {
  const {
    securitySettings,
    securitySettingsIsLoading,
    securitySettingsError,
    updateSecuritySettings,
    updateSecuritySettingsIsPending,
    checkIpAllowlist,
    checkIpAllowlistIsPending,
  } = useOrgSecuritySettings()

  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingIndex, setEditingIndex] = useState<number | null>(null)
  const [deletingIndex, setDeletingIndex] = useState<number | null>(null)
  const [checkIp, setCheckIp] = useState("")
  const [checkResult, setCheckResult] = useState<IPAllowlistCheckResult | null>(
    null
  )

  if (securitySettingsIsLoading) {
    return <CenteredSpinner />
  }
  if (securitySettingsError || !securitySettings) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading security settings: ${securitySettingsError?.message ?? "unknown error"}`}
      />
    )
  }

  const enabled = securitySettings.ip_allowlist_enabled
  const allowlists = securitySettings.ip_allowlists

  /** Persist the full policy; returns false when the API rejected it (toast already shown). */
  async function saveAllowlists(
    next: IPAllowlist[],
    nextEnabled = enabled
  ): Promise<boolean> {
    try {
      await updateSecuritySettings({
        ip_allowlist_enabled: nextEnabled,
        ip_allowlists: next,
      })
    } catch {
      return false
    }
    setCheckResult(null)
    return true
  }

  async function onToggleEnabled(checked: boolean) {
    await saveAllowlists(allowlists, checked)
  }

  async function onSaveAllowlist(values: AllowlistFormValues) {
    const entry: IPAllowlist = {
      name: values.name,
      description: values.description || null,
      cidrs: parseCidrLines(values.cidrs),
    }
    const next =
      editingIndex === null
        ? [...allowlists, entry]
        : allowlists.map((item, i) => (i === editingIndex ? entry : item))
    if (await saveAllowlists(next)) {
      setDialogOpen(false)
      setEditingIndex(null)
    }
  }

  async function onConfirmDelete() {
    if (deletingIndex === null) {
      return
    }
    await saveAllowlists(allowlists.filter((_, i) => i !== deletingIndex))
    setDeletingIndex(null)
  }

  function openCreate() {
    setEditingIndex(null)
    setDialogOpen(true)
  }

  function openEdit(index: number) {
    setEditingIndex(index)
    setDialogOpen(true)
  }

  async function onCheckIp() {
    if (!checkIp.trim()) {
      return
    }
    try {
      setCheckResult(await checkIpAllowlist({ ip_address: checkIp.trim() }))
    } catch {
      setCheckResult(null)
    }
  }

  const editing = editingIndex === null ? null : allowlists[editingIndex]
  const deleting = deletingIndex === null ? null : allowlists[deletingIndex]

  return (
    <div className="space-y-12">
      <div className="flex items-center justify-between rounded-lg border p-4">
        <div className="space-y-0.5">
          <Label htmlFor="ip-allowlist-enabled">Enforce IP allowlist</Label>
          <p className="text-sm text-muted-foreground">
            Only allow sign-in and API access to this organization from the IP
            ranges in the allowlists below. Your current IP must be covered
            before enforcement can be enabled. Platform superusers are exempt.
          </p>
        </div>
        <Switch
          id="ip-allowlist-enabled"
          checked={enabled}
          disabled={updateSecuritySettingsIsPending}
          onCheckedChange={onToggleEnabled}
        />
      </div>

      {enabled && allowlists.length === 0 && (
        <Alert>
          <ShieldAlertIcon className="size-4" />
          <AlertTitle>No IP allowlists configured</AlertTitle>
          <AlertDescription>
            Enforcement has no effect until at least one allowlist is created.
          </AlertDescription>
        </Alert>
      )}

      <div className="space-y-4">
        <div className="flex items-start justify-between gap-4">
          <div className="space-y-1">
            <h3 className="text-lg font-semibold tracking-tight">
              IP allowlists
            </h3>
            <p className="text-sm text-muted-foreground">
              Group IP addresses or CIDR ranges by purpose, for example a VPN
              egress or an office network. Up to {MAX_ALLOWLISTS} allowlists
              with {MAX_CIDRS_PER_ALLOWLIST} ranges each.
            </p>
          </div>
          <Button
            type="button"
            size="sm"
            onClick={openCreate}
            disabled={allowlists.length >= MAX_ALLOWLISTS}
          >
            <PlusIcon className="mr-2 size-4" />
            Create allowlist
          </Button>
        </div>

        {allowlists.length === 0 ? (
          <div className="rounded-lg border border-dashed p-8 text-center">
            <p className="text-sm font-medium">No IP allowlists</p>
            <p className="text-sm text-muted-foreground">
              Create an allowlist before enforcing IP restrictions.
            </p>
          </div>
        ) : (
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Description</TableHead>
                  <TableHead>IP ranges</TableHead>
                  <TableHead className="w-24" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {allowlists.map((item, index) => (
                  <TableRow key={item.name}>
                    <TableCell className="font-medium">{item.name}</TableCell>
                    <TableCell className="max-w-xs text-muted-foreground">
                      {item.description || (
                        <span className="italic">No description</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-col gap-0.5 font-mono text-xs">
                        {item.cidrs.map((cidr) => (
                          <span key={cidr}>{cidr}</span>
                        ))}
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="flex justify-end gap-1">
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          aria-label={`Edit ${item.name}`}
                          onClick={() => openEdit(index)}
                        >
                          <PencilIcon className="size-4" />
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          aria-label={`Delete ${item.name}`}
                          onClick={() => setDeletingIndex(index)}
                        >
                          <Trash2Icon className="size-4" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </div>

      <div className="space-y-3">
        <div>
          <h3 className="text-lg font-semibold tracking-tight">
            Validate an IP address
          </h3>
          <p className="text-sm text-muted-foreground">
            Check whether an IP address is admitted by the saved allowlists.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Input
            value={checkIp}
            onChange={(e) => {
              setCheckIp(e.target.value)
              setCheckResult(null)
            }}
            placeholder="8.8.8.8 or 2001:db8::1"
            className="max-w-sm font-mono"
            autoComplete="off"
            spellCheck={false}
          />
          <Button
            type="button"
            variant="outline"
            onClick={onCheckIp}
            disabled={checkIpAllowlistIsPending || !checkIp.trim()}
          >
            Check
          </Button>
        </div>
        {checkResult && <IpCheckResult result={checkResult} />}
      </div>

      {dialogOpen && (
        <AllowlistDialog
          open={dialogOpen}
          onOpenChange={(open) => {
            setDialogOpen(open)
            if (!open) {
              setEditingIndex(null)
            }
          }}
          initial={editing}
          existingNames={allowlists
            .filter((_, i) => i !== editingIndex)
            .map((item) => item.name.toLowerCase())}
          isPending={updateSecuritySettingsIsPending}
          onSubmit={onSaveAllowlist}
        />
      )}

      <AlertDialog
        open={deletingIndex !== null}
        onOpenChange={(open) => {
          if (!open) {
            setDeletingIndex(null)
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete IP allowlist</AlertDialogTitle>
            <AlertDialogDescription>
              {deleting?.name} and its {deleting?.cidrs.length ?? 0} IP{" "}
              {deleting?.cidrs.length === 1 ? "range" : "ranges"} will be
              removed.
              {enabled && allowlists.length === 1
                ? " This is the last allowlist; enforcement will no longer restrict access."
                : ""}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={onConfirmDelete}
              disabled={updateSecuritySettingsIsPending}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

interface AllowlistDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  initial: IPAllowlist | null
  existingNames: string[]
  isPending: boolean
  onSubmit: (values: AllowlistFormValues) => Promise<void>
}

function AllowlistDialog({
  open,
  onOpenChange,
  initial,
  existingNames,
  isPending,
  onSubmit,
}: AllowlistDialogProps) {
  const form = useForm<AllowlistFormValues>({
    resolver: zodResolver(
      allowlistFormSchema.refine(
        (values) => !existingNames.includes(values.name.toLowerCase()),
        {
          message: "An allowlist with this name already exists",
          path: ["name"],
        }
      )
    ),
    defaultValues: {
      name: initial?.name ?? "",
      description: initial?.description ?? "",
      cidrs: initial?.cidrs.join("\n") ?? "",
    },
  })

  const rangeCount = parseCidrLines(form.watch("cidrs")).length

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {initial ? "Edit IP allowlist" : "Create IP allowlist"}
          </DialogTitle>
          <DialogDescription>
            Name this allowlist and add the IP addresses or CIDR ranges it
            covers. A description helps you remember why each range exists.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(onSubmit)}
            className="space-y-5"
            noValidate
          >
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Name</FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      placeholder="Corporate VPN"
                      autoComplete="off"
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="description"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Description (optional)</FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      placeholder="Egress ranges for the office VPN, owned by IT"
                      autoComplete="off"
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="cidrs"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Allowed IP ranges</FormLabel>
                  <FormControl>
                    <Textarea
                      {...field}
                      rows={5}
                      placeholder={"203.0.113.0/24\n2001:db8::/32"}
                      className="font-mono"
                      autoComplete="off"
                      spellCheck={false}
                    />
                  </FormControl>
                  <FormDescription>
                    One IPv4 or IPv6 address or CIDR range per line.
                    {rangeCount > 0 &&
                      ` ${rangeCount} ${rangeCount === 1 ? "range" : "ranges"}.`}
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={isPending}>
                {initial ? "Save" : "Create"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}

function IpCheckResult({ result }: { result: IPAllowlistCheckResult }) {
  if (!result.enforced) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <CheckCircle2Icon className="size-4" />
        Allowed. The allowlist is not currently enforced.
      </p>
    )
  }
  if (result.allowed) {
    return (
      <p className="flex items-center gap-2 text-sm text-emerald-700">
        <CheckCircle2Icon className="size-4" />
        Allowed by {result.matched_cidr}
        {result.matched_allowlist ? ` (${result.matched_allowlist})` : ""}
      </p>
    )
  }
  return (
    <p className="flex items-center gap-2 text-sm text-destructive">
      <XCircleIcon className="size-4" />
      Not allowed by the current allowlists
    </p>
  )
}
