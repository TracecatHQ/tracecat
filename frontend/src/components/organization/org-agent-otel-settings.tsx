"use client"

import { Trash2 } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import type { AgentOtelSettingsRead } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { AlertNotification } from "@/components/notifications"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { toast } from "@/components/ui/use-toast"
import { useOrgAgentOtelSettings } from "@/hooks/use-org-agent-otel-settings"
import {
  type AgentOtelForm,
  type AgentOtelPrivacyFlagKey,
  type AgentOtelSignals,
  type AgentOtelTemporality,
  agentOtelConfigToEnvMap,
  emptyAgentOtelForm,
  envMapToAgentOtelConfig,
  envMapToForm,
  formToEnvMap,
  newResourceAttributeRow,
  validateAgentOtelHeaderEntries,
  validateForm,
} from "@/lib/agent-otel"
import { cn } from "@/lib/utils"

/** Radix Select forbids an empty item value, so unset is a named sentinel. */
const UNSET_OPTION = "default"

const SIGNAL_LABELS: { key: keyof AgentOtelSignals; label: string }[] = [
  { key: "traces", label: "Traces" },
  { key: "metrics", label: "Metrics" },
  { key: "logs", label: "Logs" },
]

const PRIVACY_FLAG_LABELS: { key: AgentOtelPrivacyFlagKey; label: string }[] = [
  { key: "metricsIncludeSessionId", label: "Include session ID in metrics" },
  { key: "metricsIncludeVersion", label: "Include version in metrics" },
  { key: "metricsIncludeAccountUuid", label: "Include account ID in metrics" },
  { key: "logUserPrompts", label: "Log user prompts" },
  { key: "logToolDetails", label: "Log tool details" },
  { key: "logToolContent", label: "Log tool content" },
]

/** A structured, write-only collector header row. */
interface HeaderRow {
  id: string
  name: string
  value: string
}

/** Generate a stable client-side id for a new header row. */
function newHeaderRow(): HeaderRow {
  return { id: crypto.randomUUID(), name: "", value: "" }
}

/**
 * Organization-level agent telemetry settings form. Presents the flat OTel
 * `env` map as dedicated form controls, offers a one-shot paste import for
 * `KEY=value` text, and exposes write-only collector headers as structured
 * name/value rows.
 */
export function OrgAgentOtelSettings() {
  const canUpdateSettings = useScopeCheck("org:settings:update")
  const {
    agentOtelSettings,
    agentOtelSettingsIsLoading,
    updateAgentOtelSettings,
    updateAgentOtelSettingsIsPending,
  } = useOrgAgentOtelSettings()
  const [enabled, setEnabled] = useState(false)
  const [form, setForm] = useState<AgentOtelForm>(emptyAgentOtelForm)
  const [headerRows, setHeaderRows] = useState<HeaderRow[]>([])
  const [clearSavedHeaders, setClearSavedHeaders] = useState(false)
  const [dirty, setDirty] = useState(false)
  const settingsLoadFailed =
    !agentOtelSettingsIsLoading && agentOtelSettings === undefined
  // Pending covers the PATCH plus the refetch it awaits, so the seeding
  // effect can't clobber edits made while a save is in flight.
  const editingDisabled =
    canUpdateSettings !== true ||
    agentOtelSettingsIsLoading ||
    settingsLoadFailed ||
    updateAgentOtelSettingsIsPending
  const fieldsDisabled = !enabled || editingDisabled
  // Signature of the last server config folded into local state.
  const lastServerSig = useRef<string | undefined>(undefined)

  function seedFromServer(settings: AgentOtelSettingsRead | undefined) {
    lastServerSig.current = JSON.stringify(settings?.agent_otel_config ?? null)
    setEnabled(settings?.agent_otel_config?.enabled ?? false)
    setForm(envMapToForm(agentOtelConfigToEnvMap(settings?.agent_otel_config)))
    setHeaderRows([])
    setClearSavedHeaders(false)
    setDirty(false)
  }

  // Re-seed whenever the server config changes, but never over unsaved edits;
  // a dirty form is rehydrated only by an explicit save or reset.
  useEffect(() => {
    if (!agentOtelSettings) {
      return
    }
    const sig = JSON.stringify(agentOtelSettings.agent_otel_config ?? null)
    if (sig === lastServerSig.current || dirty) {
      return
    }
    seedFromServer(agentOtelSettings)
  }, [agentOtelSettings, dirty])

  function updateForm(patch: Partial<AgentOtelForm>) {
    setDirty(true)
    setForm((prev) => ({ ...prev, ...patch }))
  }

  function handleEnabledChange(next: boolean) {
    setDirty(true)
    setEnabled(next)
  }

  function toggleSignal(key: keyof AgentOtelSignals, checked: boolean) {
    setDirty(true)
    setForm((prev) => ({
      ...prev,
      signals: { ...prev.signals, [key]: checked },
    }))
  }

  function handleFlagChange(key: AgentOtelPrivacyFlagKey, checked: boolean) {
    setDirty(true)
    setForm((prev) => ({ ...prev, flags: { ...prev.flags, [key]: checked } }))
  }

  function handleTemporalityChange(next: string) {
    let value: AgentOtelTemporality = ""
    if (next === "delta" || next === "cumulative") {
      value = next
    }
    updateForm({ temporality: value })
  }

  function handleAttributeChange(
    id: string,
    patch: { name?: string; value?: string }
  ) {
    setDirty(true)
    setForm((prev) => ({
      ...prev,
      resourceAttributes: prev.resourceAttributes.map((row) =>
        row.id === id ? { ...row, ...patch } : row
      ),
    }))
  }

  function handleAddAttribute() {
    setDirty(true)
    setForm((prev) => ({
      ...prev,
      resourceAttributes: [
        ...prev.resourceAttributes,
        newResourceAttributeRow(),
      ],
    }))
  }

  function handleRemoveAttribute(id: string) {
    setDirty(true)
    setForm((prev) => ({
      ...prev,
      resourceAttributes: prev.resourceAttributes.filter(
        (row) => row.id !== id
      ),
    }))
  }

  function handleHeaderRowChange(id: string, patch: Partial<HeaderRow>): void {
    setDirty(true)
    setClearSavedHeaders(false)
    setHeaderRows((prev) =>
      prev.map((row) => (row.id === id ? { ...row, ...patch } : row))
    )
  }

  function handleAddHeaderRow() {
    setDirty(true)
    setClearSavedHeaders(false)
    setHeaderRows((prev) => [...prev, newHeaderRow()])
  }

  function handleRemoveHeaderRow(id: string) {
    setDirty(true)
    setHeaderRows((prev) => prev.filter((row) => row.id !== id))
  }

  function handleClearSavedHeaders() {
    setDirty(true)
    setHeaderRows([])
    setClearSavedHeaders(true)
  }

  function handleReset() {
    seedFromServer(agentOtelSettings)
  }

  // Serialize validated header rows into the API's name -> value map.
  function headerRowsToMap(): Record<string, string> {
    const map: Record<string, string> = Object.create(null)
    for (const row of headerRows) {
      if (row.name.trim() !== "") {
        // Trimmed: edge whitespace is an unsendable HTTP header value.
        map[row.name.trim()] = row.value.trim()
      }
    }
    return map
  }

  const formIssues = validateForm(form, { requireOtlpEndpoint: enabled })
  const nonEmptyHeaderRows = headerRows.filter(
    (row) => row.name.trim() !== "" || row.value.trim() !== ""
  )
  const headersDirty = nonEmptyHeaderRows.length > 0
  const headerIssues = validateAgentOtelHeaderEntries(nonEmptyHeaderRows)
  const hasIssues = formIssues.length > 0 || headerIssues.length > 0

  async function handleSave() {
    if (formIssues.length > 0) {
      toast({ title: "Invalid environment", description: formIssues[0] })
      return
    }
    if (headerIssues.length > 0) {
      toast({ title: "Invalid headers", description: headerIssues[0] })
      return
    }

    // Headers are write-only: non-blank draft rows replace the entire saved
    // map, an explicit clear sends {}, and blank rows leave it unchanged.
    let headersField: Record<string, string> | undefined
    if (clearSavedHeaders) {
      headersField = {}
    } else if (headersDirty) {
      headersField = headerRowsToMap()
    }

    await updateAgentOtelSettings({
      requestBody: {
        agent_otel_config: envMapToAgentOtelConfig(enabled, formToEnvMap(form)),
        agent_otel_headers: headersField,
      },
    })
    // The form now shows the saved values; mark it clean and let the pristine
    // resync effect adopt the canonical read when the refetch lands. A stale
    // cache read matches the pre-save baseline sig, so it cannot reseed.
    setHeaderRows([])
    setClearSavedHeaders(false)
    setDirty(false)
  }

  const saveDisabled =
    editingDisabled || updateAgentOtelSettingsIsPending || hasIssues

  return (
    <section className="space-y-8">
      {settingsLoadFailed && (
        <AlertNotification
          level="error"
          message="Agent telemetry settings could not be loaded. Editing is disabled to protect the saved configuration."
          className="m-0"
        />
      )}
      {canUpdateSettings === false && !settingsLoadFailed && (
        <AlertNotification
          message="You can view these settings, but you do not have permission to update them."
          className="m-0"
        />
      )}

      <div className="flex flex-row items-center justify-between rounded-lg border p-4">
        <div className="space-y-0.5">
          <p className="text-sm font-medium">Enable agent telemetry</p>
          <p className="text-xs text-muted-foreground">
            When off, no OTel env vars are passed to agent runs.
          </p>
        </div>
        <Switch
          aria-label="Enable agent telemetry"
          checked={enabled}
          onCheckedChange={handleEnabledChange}
          disabled={editingDisabled}
        />
      </div>

      <div
        aria-disabled={fieldsDisabled}
        className={cn(
          "space-y-4 transition-opacity",
          fieldsDisabled && "pointer-events-none opacity-50"
        )}
      >
        <div className="space-y-1">
          <h3 className="text-lg font-semibold tracking-tight">Connection</h3>
          <p className="text-sm text-muted-foreground">
            Every agent run in this organization exports to this collector.
          </p>
        </div>
        <div className="space-y-4 rounded-lg border p-4">
          <div className="space-y-1.5">
            <Label htmlFor="otel-endpoint" className="text-xs">
              Collector endpoint
            </Label>
            <Input
              id="otel-endpoint"
              value={form.endpoint}
              onChange={(e) => updateForm({ endpoint: e.target.value })}
              disabled={fieldsDisabled}
              placeholder="https://collector.example.com"
              className="text-xs"
            />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="otel-metric-interval" className="text-xs">
                Metric export interval (ms)
              </Label>
              <Input
                id="otel-metric-interval"
                type="number"
                min={1}
                step={1}
                inputMode="numeric"
                value={form.metricIntervalMs}
                onChange={(e) =>
                  updateForm({ metricIntervalMs: e.target.value })
                }
                disabled={fieldsDisabled}
                placeholder="60000"
                className="text-xs"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="otel-logs-interval" className="text-xs">
                Logs export interval (ms)
              </Label>
              <Input
                id="otel-logs-interval"
                type="number"
                min={1}
                step={1}
                inputMode="numeric"
                value={form.logsIntervalMs}
                onChange={(e) => updateForm({ logsIntervalMs: e.target.value })}
                disabled={fieldsDisabled}
                placeholder="5000"
                className="text-xs"
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="otel-temporality" className="text-xs">
              Metrics temporality
            </Label>
            <Select
              value={form.temporality || UNSET_OPTION}
              onValueChange={handleTemporalityChange}
              disabled={fieldsDisabled}
            >
              <SelectTrigger id="otel-temporality" className="text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={UNSET_OPTION}>Default</SelectItem>
                <SelectItem value="delta">Delta</SelectItem>
                <SelectItem value="cumulative">Cumulative</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label className="text-xs">Signals</Label>
            <div className="flex flex-col gap-2.5 sm:flex-row sm:gap-6">
              {SIGNAL_LABELS.map(({ key, label }) => (
                <div key={key} className="flex items-center gap-2">
                  <Checkbox
                    id={`otel-signal-${key}`}
                    checked={form.signals[key]}
                    onCheckedChange={(checked) =>
                      toggleSignal(key, checked === true)
                    }
                    disabled={fieldsDisabled}
                  />
                  <Label
                    htmlFor={`otel-signal-${key}`}
                    className="text-xs font-normal"
                  >
                    {label}
                  </Label>
                </div>
              ))}
            </div>
          </div>
          {formIssues.length > 0 && (
            <p className="text-xs text-muted-foreground">{formIssues[0]}</p>
          )}
          <div className="space-y-3 border-t pt-4">
            <div className="space-y-1">
              <p className="text-sm font-medium">Headers</p>
              <p className="text-xs text-muted-foreground">
                Encrypted, write-only collector headers. Saved values are not
                shown again, and saving new headers replaces all previously
                saved headers.
              </p>
            </div>
            {headerRows.length > 0 && (
              <div className="space-y-2">
                {headerRows.map((row) => (
                  <div key={row.id} className="flex items-center gap-2">
                    <Input
                      value={row.name}
                      onChange={(e) =>
                        handleHeaderRowChange(row.id, { name: e.target.value })
                      }
                      disabled={fieldsDisabled}
                      placeholder="Header name"
                      className="text-xs"
                    />
                    <Input
                      type="password"
                      value={row.value}
                      onChange={(e) =>
                        handleHeaderRowChange(row.id, { value: e.target.value })
                      }
                      disabled={fieldsDisabled}
                      placeholder="Header value"
                      className="text-xs"
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      onClick={() => handleRemoveHeaderRow(row.id)}
                      disabled={fieldsDisabled}
                      aria-label="Remove header"
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  </div>
                ))}
              </div>
            )}
            {headerIssues.length > 0 && (
              <p className="text-xs text-destructive" role="alert">
                {headerIssues[0]}
              </p>
            )}
            <div className="flex items-center gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={handleAddHeaderRow}
                disabled={fieldsDisabled}
              >
                Add header
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={handleClearSavedHeaders}
                disabled={fieldsDisabled || clearSavedHeaders}
                className="text-destructive hover:text-destructive"
              >
                Clear saved headers
              </Button>
            </div>
            {clearSavedHeaders && (
              <p className="text-xs text-muted-foreground">
                Saved headers will be cleared when you save.
              </p>
            )}
          </div>
        </div>
      </div>

      <div
        aria-disabled={fieldsDisabled}
        className={cn(
          "space-y-4 transition-opacity",
          fieldsDisabled && "pointer-events-none opacity-50"
        )}
      >
        <div className="space-y-1">
          <h3 className="text-lg font-semibold tracking-tight">
            Exported data
          </h3>
          <p className="text-sm text-muted-foreground">
            Prompt and tool content stay out of exported telemetry unless
            enabled here.
          </p>
        </div>
        <div className="space-y-4 rounded-lg border p-4">
          <div className="space-y-3">
            <p className="text-sm font-medium">Privacy and cardinality</p>
            <div className="grid gap-4 sm:grid-cols-2">
              {PRIVACY_FLAG_LABELS.map(({ key, label }) => (
                <div key={key} className="flex items-center gap-2">
                  <Checkbox
                    id={`otel-flag-${key}`}
                    checked={form.flags[key]}
                    onCheckedChange={(checked) =>
                      handleFlagChange(key, checked === true)
                    }
                    disabled={fieldsDisabled}
                  />
                  <Label
                    htmlFor={`otel-flag-${key}`}
                    className="text-xs font-normal"
                  >
                    {label}
                  </Label>
                </div>
              ))}
            </div>
          </div>
          <div className="space-y-3 border-t pt-4">
            <div className="space-y-1">
              <p className="text-sm font-medium">Resource attributes</p>
              <p className="text-xs text-muted-foreground">
                Attached to every exported signal, for example service.name.
              </p>
            </div>
            {form.resourceAttributes.length > 0 && (
              <div className="space-y-2">
                {form.resourceAttributes.map((row) => (
                  <div key={row.id} className="flex items-center gap-2">
                    <Input
                      value={row.name}
                      onChange={(e) =>
                        handleAttributeChange(row.id, { name: e.target.value })
                      }
                      disabled={fieldsDisabled}
                      placeholder="Attribute name"
                      className="text-xs"
                    />
                    <Input
                      value={row.value}
                      onChange={(e) =>
                        handleAttributeChange(row.id, { value: e.target.value })
                      }
                      disabled={fieldsDisabled}
                      placeholder="Attribute value"
                      className="text-xs"
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      onClick={() => handleRemoveAttribute(row.id)}
                      disabled={fieldsDisabled}
                      aria-label="Remove attribute"
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  </div>
                ))}
              </div>
            )}
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={handleAddAttribute}
              disabled={fieldsDisabled}
            >
              Add attribute
            </Button>
          </div>
        </div>
      </div>

      <div className="flex justify-end gap-2">
        <Button
          type="button"
          variant="outline"
          onClick={handleReset}
          disabled={editingDisabled || !dirty}
        >
          Reset
        </Button>
        <Button type="button" onClick={handleSave} disabled={saveDisabled}>
          {updateAgentOtelSettingsIsPending ? "Saving..." : "Save config"}
        </Button>
      </div>
    </section>
  )
}
