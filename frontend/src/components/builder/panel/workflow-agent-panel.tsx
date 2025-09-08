"use client"

import { useMemo, useState } from "react"
import { ChatInterface } from "@/components/chat/chat-interface"
import { AlertNotification } from "@/components/notifications"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
import { toast } from "@/components/ui/use-toast"
import { useUpdateChat } from "@/hooks/use-chat"
import { useBuilderRegistryActions } from "@/lib/hooks"
import { useWorkflow } from "@/providers/workflow"
import { useWorkspaceId } from "@/providers/workspace-id"

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
    </div>
  )
}
