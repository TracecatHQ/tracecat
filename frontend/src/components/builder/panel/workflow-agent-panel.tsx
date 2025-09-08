"use client"

import { useMemo, useState } from "react"
import YAML from "yaml"
import type { DSLInput_Input, TextPart } from "@/client"
import { ChatInterface } from "@/components/chat/chat-interface"
import { JsonViewWithControls } from "@/components/json-viewer"
import { AlertNotification } from "@/components/notifications"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
import { toast } from "@/components/ui/use-toast"
import { useChat, useUpdateChat } from "@/hooks/use-chat"
import { useBuilderRegistryActions } from "@/lib/hooks"
import { useWorkflow } from "@/providers/workflow"
import { useWorkspaceId } from "@/providers/workspace-id"

type ParsedDsl = { raw: string; parsed?: DSLInput_Input; error?: string }

function extractLastDslFromText(raw: string): ParsedDsl | null {
  // Look for fenced code blocks with yaml/json
  const fenceRe = /```(yaml|yml|json)\n([\s\S]*?)```/gi
  let match: RegExpExecArray | null
  let last: ParsedDsl | null = null

  match = fenceRe.exec(raw)
  while (match !== null) {
    const lang = match[1].toLowerCase()
    const code = match[2]
    try {
      const parsed =
        lang === "json"
          ? JSON.parse(code)
          : (YAML.parse(code) as DSLInput_Input)
      last = { raw: code, parsed }
    } catch (e: unknown) {
      last = { raw: code, error: e instanceof Error ? e.message : String(e) }
    }
    match = fenceRe.exec(raw)
  }
  return last
}

export function WorkflowAgentPanel() {
  const { workflow, workflowId } = useWorkflow()
  const workspaceId = useWorkspaceId()
  const [chatId, setChatId] = useState<string | undefined>(undefined)
  const [selectedTools, setSelectedTools] = useState<string[]>([])
  const { updateChat } = useUpdateChat(workspaceId)

  // Available tools from registry
  const { registryActions } = useBuilderRegistryActions()
  const toolOptions = useMemo(
    () =>
      (registryActions || []).map((a) => ({
        label: `${a.namespace}.${a.name}`,
        value: `${a.namespace}__${a.name}`,
      })),
    [registryActions]
  )

  // Stream messages from the selected chat for DSL extraction
  const { messages } = useChat({ chatId, workspaceId })

  const latestAssistantText = useMemo(() => {
    // Find the last response with a text part
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i]
      if (m.kind === "response") {
        const textParts = m.parts.filter(
          (p): p is TextPart => p.part_kind === "text" && "content" in p
        )
        if (textParts.length > 0) {
          return textParts[textParts.length - 1].content
        }
      }
    }
    return ""
  }, [messages])

  const parsed = useMemo(() => {
    if (!latestAssistantText) return null
    return extractLastDslFromText(latestAssistantText)
  }, [latestAssistantText])

  const handleToolsChange = async (values: string[] | readonly string[]) => {
    const v = Array.from(values)
    setSelectedTools(v)
    if (chatId) {
      try {
        await updateChat({ chatId, update: { tools: v } })
        toast({
          title: "Updated tools",
          description: `${v.length} tool(s) selected`,
        })
      } catch (e) {
        console.error("Failed to update chat tools", e)
      }
    }
  }

  if (!workflow || !workflowId) {
    return <AlertNotification level="error" message="No workflow loaded" />
  }

  return (
    <div className="flex h-full w-full flex-col">
      <div className="flex items-center gap-3 p-3">
        <div className="flex flex-col gap-1 flex-1">
          <Label className="text-xs">Available tools</Label>
          <select
            multiple
            value={selectedTools}
            onChange={(e) =>
              handleToolsChange(
                Array.from(e.target.selectedOptions).map((o) => o.value)
              )
            }
            className="min-h-8 text-xs border rounded p-1"
          >
            {toolOptions.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>
      </div>
      <Separator />
      <div className="grid grid-cols-2 gap-2 h-full min-h-0">
        <div className="h-full min-h-0 overflow-hidden">
          <ChatInterface
            entityType="workflow"
            entityId={workflow.id}
            onChatSelect={(id) => {
              setChatId(id)
              // Apply selected tools to the newly created/selected chat
              if (selectedTools.length > 0) {
                updateChat({
                  chatId: id,
                  update: { tools: selectedTools },
                }).catch(() => {})
              }
            }}
          />
        </div>
        <div className="h-full min-h-0 overflow-auto p-3">
          <div className="text-xs font-semibold mb-2">Preview DSL</div>
          {!latestAssistantText && (
            <div className="text-xs text-muted-foreground">
              No proposed DSL found in the last response.
            </div>
          )}
          {latestAssistantText && !parsed && (
            <div className="text-xs text-muted-foreground">
              No YAML/JSON code block found in the assistant response.
            </div>
          )}
          {parsed?.error && (
            <AlertNotification
              level="error"
              message={`Parse error: ${parsed.error}`}
            />
          )}
          {parsed?.parsed && (
            <JsonViewWithControls src={parsed.parsed} defaultExpanded={true} />
          )}
        </div>
      </div>
    </div>
  )
}
