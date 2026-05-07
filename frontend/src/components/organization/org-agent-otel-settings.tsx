"use client"

import { useEffect, useState } from "react"
import { CodeEditor } from "@/components/editor/codemirror/code-editor"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { Switch } from "@/components/ui/switch"
import { toast } from "@/components/ui/use-toast"
import {
  envLintExtensions,
  envMapToText,
  headerLintExtensions,
  parseEnvText,
  parseHeadersJson,
  validateEnvText,
  validateHeadersJson,
} from "@/lib/agent-otel"
import { useOrgAgentOtelSettings } from "@/lib/hooks"
import { cn } from "@/lib/utils"

const HEADERS_PLACEHOLDER = `{
  "Authorization": "Bearer ..."
}`

/**
 * Organization-level Agent OTel settings form.
 */
export function OrgAgentOtelSettings() {
  const {
    agentOtelSettings,
    agentOtelSettingsIsLoading,
    updateAgentOtelSettings,
    updateAgentOtelSettingsIsPending,
  } = useOrgAgentOtelSettings()

  const [enabled, setEnabled] = useState(false)
  const [env, setEnv] = useState("")
  const [headers, setHeaders] = useState("")
  const [headersDirty, setHeadersDirty] = useState(false)

  // Seed form state from server values once they load.
  useEffect(() => {
    if (!agentOtelSettings) {
      return
    }
    setEnabled(agentOtelSettings.agent_otel_config?.enabled ?? false)
    setEnv(envMapToText(agentOtelSettings.agent_otel_config?.env ?? {}))
    setHeaders("")
    setHeadersDirty(false)
  }, [agentOtelSettings])

  function handleHeadersChange(next: string) {
    setHeaders(next)
    setHeadersDirty(true)
  }

  function handleReset() {
    setEnabled(agentOtelSettings?.agent_otel_config?.enabled ?? false)
    setEnv(envMapToText(agentOtelSettings?.agent_otel_config?.env ?? {}))
    setHeaders("")
    setHeadersDirty(false)
  }

  const envIssues = validateEnvText(env)
  const headerIssues =
    headersDirty && headers.trim() !== "" ? validateHeadersJson(headers) : []
  const hasIssues = envIssues.length > 0 || headerIssues.length > 0

  async function handleSave() {
    if (envIssues.length > 0) {
      const first = envIssues[0]
      toast({
        title: "Invalid environment",
        description: `Line ${first.lineNumber}: ${first.message}`,
      })
      return
    }
    if (headerIssues.length > 0) {
      toast({ title: "Invalid headers", description: headerIssues[0] })
      return
    }

    // Headers semantics: untouched -> omit; cleared -> {}; non-empty -> replace.
    let headersField: Record<string, string> | null | undefined
    if (!headersDirty) {
      headersField = undefined
    } else if (headers.trim() === "") {
      headersField = {}
    } else {
      headersField = parseHeadersJson(headers)
    }

    await updateAgentOtelSettings({
      requestBody: {
        agent_otel_config: { enabled, env: parseEnvText(env) },
        agent_otel_headers: headersField,
      },
    })
    setHeaders("")
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
        <div className="space-y-1 p-4">
          <p className="text-sm font-medium">Headers</p>
          <p className="text-xs text-muted-foreground">
            Encrypted, write-only collector headers. Saved values are not shown
            again.
          </p>
        </div>
        <Separator />
        <div className="space-y-3 p-4">
          <CodeEditor
            value={headers}
            onChange={handleHeadersChange}
            language="json"
            wrapLongLines
            readOnly={!enabled}
            placeholder={HEADERS_PLACEHOLDER}
            extensions={headerLintExtensions}
            className="font-mono text-xs [&_.cm-content]:text-xs [&_.cm-editor]:min-h-[120px]"
          />
        </div>
      </div>

      <div
        aria-disabled={!enabled}
        className={cn(
          "rounded-lg border transition-opacity",
          !enabled && "pointer-events-none opacity-50"
        )}
      >
        <div className="space-y-1 p-4">
          <p className="text-sm font-medium">Environment</p>
          <p className="text-xs text-muted-foreground">
            OTel-compatible env vars passed to the agent runtime. See the{" "}
            <a
              className="underline underline-offset-2"
              href="https://code.claude.com/docs/en/monitoring-usage"
              rel="noreferrer"
              target="_blank"
            >
              Claude Code monitoring docs
            </a>{" "}
            for supported options.
          </p>
        </div>
        <Separator />
        <div className="space-y-3 p-4">
          <CodeEditor
            value={env}
            onChange={setEnv}
            language="text"
            wrapLongLines
            readOnly={!enabled}
            extensions={envLintExtensions}
            className="font-mono text-xs [&_.cm-content]:text-xs [&_.cm-editor]:min-h-[260px]"
          />
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
