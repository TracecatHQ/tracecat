"use client"

import { Code2, SlidersHorizontal, Trash2 } from "lucide-react"
import { useEffect, useState } from "react"
import { CodeEditor } from "@/components/editor/codemirror/code-editor"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
import { Switch } from "@/components/ui/switch"
import { type ToggleTabOption, ToggleTabs } from "@/components/ui/toggle-tabs"
import { toast } from "@/components/ui/use-toast"
import {
  type AgentOtelForm,
  type AgentOtelSignals,
  envLintExtensions,
  envMapToForm,
  envTextToForm,
  formToEnvMap,
  formToEnvText,
  parseHeadersJson,
  validateEnvText,
  validateForm,
  validateHeadersJson,
} from "@/lib/agent-otel"
import { useOrgAgentOtelSettings } from "@/lib/hooks"
import { cn } from "@/lib/utils"

/** Which editing surface the env config is shown in. */
type EditMode = "form" | "raw"

const MODE_OPTIONS: ToggleTabOption<EditMode>[] = [
  {
    value: "form",
    content: (
      <div className="flex items-center gap-1">
        <SlidersHorizontal className="size-3" />
        <span className="text-xs">Form</span>
      </div>
    ),
    tooltip: "Edit with form fields",
    ariaLabel: "Form mode",
  },
  {
    value: "raw",
    content: (
      <div className="flex items-center gap-1">
        <Code2 className="size-3" />
        <span className="text-xs">Raw</span>
      </div>
    ),
    tooltip: "Edit every variable as text",
    ariaLabel: "Raw mode",
  },
]

const SIGNAL_LABELS: { key: keyof AgentOtelSignals; label: string }[] = [
  { key: "traces", label: "Traces" },
  { key: "metrics", label: "Metrics" },
  { key: "logs", label: "Logs" },
]

const EMPTY_FORM: AgentOtelForm = {
  endpoint: "",
  metricIntervalMs: "",
  signals: { traces: false, metrics: false, logs: false },
  advancedEnv: "",
}

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
 * Organization-level Agent OTel settings form. Presents the flat OTel `env`
 * map as first-class connection fields with a raw Advanced escape hatch, and
 * exposes write-only collector headers as structured name/value rows.
 */
export function OrgAgentOtelSettings() {
  const {
    agentOtelSettings,
    agentOtelSettingsIsLoading,
    updateAgentOtelSettings,
    updateAgentOtelSettingsIsPending,
  } = useOrgAgentOtelSettings()

  const [enabled, setEnabled] = useState(false)
  const [form, setForm] = useState<AgentOtelForm>(EMPTY_FORM)
  // The env config has a single source of truth: `form`. Raw mode edits its own
  // text buffer (seeded on entry, folded back into `form` on exit/save) so
  // typing isn't fought by a re-parse on every keystroke.
  const [mode, setMode] = useState<EditMode>("form")
  const [rawEnv, setRawEnv] = useState("")
  const [headerRows, setHeaderRows] = useState<HeaderRow[]>([])
  const [headersDirty, setHeadersDirty] = useState(false)

  // Seed form state from server values once they load.
  useEffect(() => {
    if (!agentOtelSettings) {
      return
    }
    setEnabled(agentOtelSettings.agent_otel_config?.enabled ?? false)
    setForm(envMapToForm(agentOtelSettings.agent_otel_config?.env ?? {}))
    setMode("form")
    setRawEnv("")
    setHeaderRows([])
    setHeadersDirty(false)
  }, [agentOtelSettings])

  function updateForm(patch: Partial<AgentOtelForm>) {
    setForm((prev) => ({ ...prev, ...patch }))
  }

  // Switch editing surface, folding the current representation into the other.
  // `form` stays the source of truth; the raw buffer is derived on entry and
  // parsed back on exit.
  function syncMode(next: EditMode) {
    if (next === mode) {
      return
    }
    if (next === "raw") {
      setRawEnv(formToEnvText(form))
      setMode("raw")
      return
    }
    // Leaving raw: parse the buffer back into structured fields.
    setForm(envTextToForm(rawEnv))
    setMode("form")
  }

  function toggleSignal(key: keyof AgentOtelSignals, checked: boolean) {
    setForm((prev) => ({
      ...prev,
      signals: { ...prev.signals, [key]: checked },
    }))
  }

  function handleHeaderRowChange(id: string, patch: Partial<HeaderRow>): void {
    setHeadersDirty(true)
    setHeaderRows((prev) =>
      prev.map((row) => (row.id === id ? { ...row, ...patch } : row))
    )
  }

  function handleAddHeaderRow() {
    setHeadersDirty(true)
    setHeaderRows((prev) => [...prev, newHeaderRow()])
  }

  function handleRemoveHeaderRow(id: string) {
    setHeadersDirty(true)
    setHeaderRows((prev) => prev.filter((row) => row.id !== id))
  }

  function handleReset() {
    setEnabled(agentOtelSettings?.agent_otel_config?.enabled ?? false)
    setForm(envMapToForm(agentOtelSettings?.agent_otel_config?.env ?? {}))
    setMode("form")
    setRawEnv("")
    setHeaderRows([])
    setHeadersDirty(false)
  }

  // Serialize header rows into a name -> value map for validation and save.
  function headerRowsToJson(): string {
    const map: Record<string, string> = {}
    for (const row of headerRows) {
      if (row.name.trim() !== "") {
        map[row.name.trim()] = row.value
      }
    }
    return Object.keys(map).length === 0 ? "" : JSON.stringify(map)
  }

  // Env validation is mode-aware. In Raw mode the buffer is the source of truth,
  // so we run the line-oriented `validateEnvText` over it (catches malformed
  // lines and duplicate keys that a collapsed map cannot). In Form mode we run
  // the merged-map rules via `validateForm`, and additionally line-validate the
  // Advanced tail so dupes/malformed lines there are blocked client-side instead
  // of being silently dropped on save (the backend would reject them anyway).
  const rawIssues = mode === "raw" ? validateEnvText(rawEnv) : []
  const advancedIssues =
    mode === "form" ? validateEnvText(form.advancedEnv) : []
  const formIssues = mode === "form" ? validateForm(form) : []
  const headersJson = headerRowsToJson()
  const headerIssues =
    headersDirty && headersJson !== "" ? validateHeadersJson(headersJson) : []
  const hasIssues =
    rawIssues.length > 0 ||
    advancedIssues.length > 0 ||
    formIssues.length > 0 ||
    headerIssues.length > 0

  async function handleSave() {
    // Raw mode saves the buffer; Form mode saves the structured fields.
    const lineIssues = mode === "raw" ? rawIssues : advancedIssues
    if (lineIssues.length > 0) {
      const first = lineIssues[0]
      toast({
        title: "Invalid environment",
        description: `Line ${first.lineNumber}: ${first.message}`,
      })
      return
    }
    if (formIssues.length > 0) {
      toast({ title: "Invalid environment", description: formIssues[0] })
      return
    }
    if (headerIssues.length > 0) {
      toast({ title: "Invalid headers", description: headerIssues[0] })
      return
    }

    const env =
      mode === "raw" ? formToEnvMap(envTextToForm(rawEnv)) : formToEnvMap(form)

    // Headers semantics: untouched -> omit; cleared -> {}; non-empty -> replace.
    let headersField: Record<string, string> | null | undefined
    if (!headersDirty) {
      headersField = undefined
    } else if (headersJson === "") {
      headersField = {}
    } else {
      headersField = parseHeadersJson(headersJson)
    }

    await updateAgentOtelSettings({
      requestBody: {
        agent_otel_config: { enabled, env },
        agent_otel_headers: headersField,
      },
    })
    setHeaderRows([])
    setHeadersDirty(false)
  }

  const saveDisabled =
    agentOtelSettingsIsLoading || updateAgentOtelSettingsIsPending || hasIssues

  return (
    <section className="space-y-4">
      <div className="space-y-1">
        <h3 className="text-lg font-semibold tracking-tight">
          Agent telemetry
        </h3>
        <p className="text-sm text-muted-foreground">
          Export agent runtime telemetry with OTel-compatible environment
          variables.
        </p>
      </div>

      <div className="flex flex-row items-center justify-between rounded-lg border p-4">
        <div className="space-y-0.5">
          <p className="text-sm font-medium">Enable agent telemetry</p>
          <p className="text-xs text-muted-foreground">
            When off, no OTel env vars are passed to agent runs.
          </p>
        </div>
        <Switch checked={enabled} onCheckedChange={setEnabled} />
      </div>

      <div
        aria-disabled={!enabled}
        className={cn(
          "rounded-lg border transition-opacity",
          !enabled && "pointer-events-none opacity-50"
        )}
      >
        <div className="flex items-start justify-between gap-4 p-4">
          <div className="space-y-1">
            <p className="text-sm font-medium">Connection</p>
            <p className="text-xs text-muted-foreground">
              Point the agent at your OTLP collector and choose which signals to
              export. Switch to Raw to edit every variable as text. See the{" "}
              <a
                className="underline underline-offset-2"
                href="https://code.claude.com/docs/en/monitoring-usage"
                rel="noreferrer"
                target="_blank"
              >
                Claude Code monitoring docs
              </a>
              .
            </p>
          </div>
          <ToggleTabs
            options={MODE_OPTIONS}
            value={mode}
            onValueChange={syncMode}
            size="sm"
            className="shrink-0"
          />
        </div>
        <Separator />
        {mode === "form" ? (
          <div className="space-y-4 p-4">
            <div className="space-y-1.5">
              <Label htmlFor="otel-endpoint" className="text-xs">
                Collector endpoint
              </Label>
              <Input
                id="otel-endpoint"
                value={form.endpoint}
                onChange={(e) => updateForm({ endpoint: e.target.value })}
                disabled={!enabled}
                placeholder="https://collector.example.com"
                className="text-xs"
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="otel-metric-interval" className="text-xs">
                Export interval (ms)
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
                disabled={!enabled}
                placeholder="60000"
                className="text-xs"
              />
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
                      disabled={!enabled}
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
          </div>
        ) : (
          <div className="space-y-3 p-4">
            <CodeEditor
              value={rawEnv}
              onChange={setRawEnv}
              language="text"
              wrapLongLines
              readOnly={!enabled}
              extensions={envLintExtensions}
              className="font-mono text-xs [&_.cm-content]:text-xs [&_.cm-editor]:min-h-[240px]"
            />
          </div>
        )}
      </div>

      <div
        aria-disabled={!enabled}
        className={cn(
          "rounded-lg border transition-opacity",
          !enabled && "pointer-events-none opacity-50"
        )}
      >
        <div className="space-y-1 p-4">
          <p className="text-sm font-medium">Headers</p>
          <p className="text-xs text-muted-foreground">
            Encrypted, write-only collector headers. Saved values are not shown
            again.
          </p>
        </div>
        <Separator />
        <div className="space-y-3 p-4">
          {headerRows.length > 0 && (
            <div className="space-y-2">
              {headerRows.map((row) => (
                <div key={row.id} className="flex items-center gap-2">
                  <Input
                    value={row.name}
                    onChange={(e) =>
                      handleHeaderRowChange(row.id, { name: e.target.value })
                    }
                    disabled={!enabled}
                    placeholder="Header name"
                    className="text-xs"
                  />
                  <Input
                    type="password"
                    value={row.value}
                    onChange={(e) =>
                      handleHeaderRowChange(row.id, { value: e.target.value })
                    }
                    disabled={!enabled}
                    placeholder="Header value"
                    className="text-xs"
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    onClick={() => handleRemoveHeaderRow(row.id)}
                    disabled={!enabled}
                    aria-label="Remove header"
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
            onClick={handleAddHeaderRow}
            disabled={!enabled}
          >
            Add header
          </Button>
        </div>
      </div>

      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" onClick={handleReset}>
          Reset
        </Button>
        <Button type="button" onClick={handleSave} disabled={saveDisabled}>
          {updateAgentOtelSettingsIsPending ? "Saving..." : "Save config"}
        </Button>
      </div>
    </section>
  )
}
