"use client"

import { CopyIcon, RotateCcwIcon } from "lucide-react"
import { CodeBlock } from "@/components/code-block"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { toast } from "@/components/ui/use-toast"
import { useOrgMcpConnect } from "@/lib/hooks"

type SetupSnippetKey = "codex" | "claude_code"

const SETUP_SNIPPET_DETAILS: {
  key: SetupSnippetKey
  label: string
  description: string
}[] = [
  {
    key: "claude_code",
    label: "Claude Code",
    description: "Add this to your Claude Code MCP config.",
  },
  {
    key: "codex",
    label: "Codex",
    description: "Add this to your Codex MCP config.",
  },
]

const MCP_VERIFY_COMMAND = "/mcp"

async function copyToClipboard(value: string, description: string) {
  try {
    await navigator.clipboard.writeText(value)
    toast({
      title: "Copied",
      description,
    })
  } catch (error) {
    console.error("Failed to copy text to clipboard", error)
    toast({
      title: "Copy failed",
      description: "Could not copy to clipboard.",
      variant: "destructive",
    })
  }
}

export function OrgSettingsMCP() {
  const {
    mcpConnect,
    mcpConnectIsLoading,
    mcpConnectError,
    refetchMcpConnect,
  } = useOrgMcpConnect()

  if (mcpConnectIsLoading) {
    return <CenteredSpinner />
  }

  if (mcpConnectError || !mcpConnect) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading MCP connection details: ${
          mcpConnectError instanceof Error
            ? mcpConnectError.message
            : "Unknown error"
        }`}
      />
    )
  }

  return (
    <div className="space-y-8">
      <div className="space-y-3">
        <Label>Scoped server URL</Label>
        <div className="flex items-center gap-2">
          <Input value={mcpConnect.scoped_server_url} readOnly />
          <Button
            type="button"
            variant="outline"
            onClick={() =>
              copyToClipboard(
                mcpConnect.scoped_server_url,
                "Scoped MCP server URL copied."
              )
            }
          >
            <CopyIcon className="mr-2 size-4" />
            Copy
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={() => void refetchMcpConnect()}
          >
            <RotateCcwIcon className="mr-2 size-4" />
            Regenerate
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          Scope expires at{" "}
          {new Date(mcpConnect.scope_expires_at).toLocaleString()}.
        </p>
      </div>

      <div className="space-y-6">
        <div className="space-y-2">
          <Label>MCP setup</Label>
          <p className="text-sm text-muted-foreground">
            Add one of these config blocks in your local client. Then run{" "}
            <code className="font-mono">{MCP_VERIFY_COMMAND}</code> to verify
            the Tracecat server appears in your MCP list.
          </p>
        </div>

        {SETUP_SNIPPET_DETAILS.map(({ key, label, description }) => {
          const snippet = mcpConnect.snippets[key]
          return (
            <div key={key} className="space-y-2">
              <div className="flex items-center justify-between">
                <div className="space-y-1">
                  <Label>{label}</Label>
                  <p className="text-xs text-muted-foreground">{description}</p>
                </div>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    copyToClipboard(snippet, `${label} setup block copied.`)
                  }
                >
                  <CopyIcon className="mr-2 size-3.5" />
                  Copy
                </Button>
              </div>
              <CodeBlock>
                <code className="whitespace-pre text-xs">{snippet}</code>
              </CodeBlock>
            </div>
          )
        })}

        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Label>Verify</Label>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() =>
                copyToClipboard(MCP_VERIFY_COMMAND, "Verify command copied.")
              }
            >
              <CopyIcon className="mr-2 size-3.5" />
              Copy
            </Button>
          </div>
          <CodeBlock>
            <code className="whitespace-pre text-xs">{MCP_VERIFY_COMMAND}</code>
          </CodeBlock>
        </div>
      </div>
    </div>
  )
}
