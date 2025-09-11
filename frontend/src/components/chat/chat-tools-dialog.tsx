"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useMemo } from "react"
import { Controller, useForm } from "react-hook-form"
import { z } from "zod"
import { ActionMultiselect } from "@/components/chat/action-multiselect"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogFooter } from "@/components/ui/dialog"
import { Form, FormItem, FormMessage } from "@/components/ui/form"
import { useGetChat, useUpdateChat } from "@/hooks/use-chat"
import { useWorkspaceId } from "@/providers/workspace-id"

interface ChatToolsDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  chatId: string
}

const chatToolsSchema = z.object({
  tools: z.array(z.string()).default([]),
})

type ChatToolsSchema = z.infer<typeof chatToolsSchema>

export function ChatToolsDialog({
  open,
  onOpenChange,
  chatId,
}: ChatToolsDialogProps) {
  const workspaceId = useWorkspaceId()
  const { chat } = useGetChat({ chatId, workspaceId })
  const { updateChat, isUpdating } = useUpdateChat(workspaceId)

  // cache the server tools so the reference stays stable
  const serverTools = useMemo<string[]>(() => chat?.tools ?? [], [chat?.tools])

  const form = useForm<ChatToolsSchema>({
    resolver: zodResolver(chatToolsSchema),
    values: { tools: serverTools },
  })

  const handleSave = async (values: { tools: string[] }) => {
    try {
      await updateChat({
        chatId,
        update: { tools: values.tools },
      })
      onOpenChange(false)
    } catch (error) {
      console.error("Failed to update chat tools:", error)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="sm:max-w-[800px]"
        aria-label="Configure chat tools"
      >
        <Form {...form}>
          <form onSubmit={form.handleSubmit(handleSave)} className="space-y-4">
            <div className="py-4">
              <Controller
                control={form.control}
                name="tools"
                render={({ field }) => (
                  <FormItem>
                    <ActionMultiselect
                      className="min-w-[600px]"
                      field={field}
                      searchKeys={["value", "label", "description", "group"]}
                    />
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <DialogFooter>
              <Button
                type="button"
                variant="ghost"
                onClick={() => {
                  form.reset({ tools: serverTools })
                  onOpenChange(false)
                }}
                disabled={isUpdating}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={isUpdating}>
                {isUpdating ? "Saving..." : "Save Tools"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
