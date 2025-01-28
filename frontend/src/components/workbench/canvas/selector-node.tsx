import React, { useCallback, useEffect, useMemo, useRef } from "react"
import { actionsCreateAction, RegistryActionRead } from "@/client"
import { useWorkflowBuilder } from "@/providers/builder"
import fuzzysort from "fuzzysort"
import { CloudOffIcon, XIcon } from "lucide-react"
import { Handle, Node, NodeProps, Position, useNodeId } from "reactflow"

import { useWorkbenchRegistryActions } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import { Label } from "@/components/ui/label"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { toast } from "@/components/ui/use-toast"
import { getIcon } from "@/components/icons"
import { ActionNodeType } from "@/components/workbench/canvas/action-node"
import { isEphemeral } from "@/components/workbench/canvas/canvas"

export const SelectorTypename = "selector" as const

const highlight = true
const SEARCH_KEYS = [
  "action",
  "default_title",
  "display_group",
] as (keyof RegistryActionRead)[]

function filterActions(actions: RegistryActionRead[], search: string) {
  const results = fuzzysort.go<RegistryActionRead>(search, actions, {
    all: true,
    keys: SEARCH_KEYS,
  })
  return results
}

export interface SelectorNodeData {
  type: "selector"
}
export type SelectorNodeType = Node<SelectorNodeData>

export default React.memo(function SelectorNode({
  targetPosition,
}: NodeProps<SelectorNodeData>) {
  const id = useNodeId()
  const { workflowId, reactFlow } = useWorkflowBuilder()
  const { setNodes, setEdges } = reactFlow
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    // Remove the selector node when the escape key is pressed
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        removeSelectorNode()
      }
    }

    window.addEventListener("keydown", handleKeyDown)

    return () => {
      window.removeEventListener("keydown", handleKeyDown)
    }
  }, [])

  useEffect(() => {
    // Focus the input after a short delay to allow the command list to open
    const timer = setTimeout(() => {
      inputRef.current?.focus()
    }, 50)
    return () => clearTimeout(timer)
  }, [])

  const removeSelectorNode = () => {
    setNodes((nodes) => nodes.filter((node) => !isEphemeral(node)))
    setEdges((edges) => edges.filter((edge) => edge.target !== id))
  }
  const [inputValue, setInputValue] = React.useState("")

  if (!workflowId || !id) {
    console.error("Workflow or node ID not found")
    return null
  }

  return (
    <div onWheelCapture={(e) => e.stopPropagation()}>
      <Command
        className="h-96 w-72 rounded-lg border shadow-sm"
        shouldFilter={false}
      >
        <div className="w-full bg-muted-foreground/5 px-3 py-[2px]">
          <Label className="flex items-center text-xs text-muted-foreground">
            <span className="font-medium">Add a node</span>
            <span className="my-px ml-auto flex items-center space-x-2">
              <p className="my-0 inline-block rounded-sm border border-muted-foreground/20 bg-muted-foreground/10 px-px py-0 font-mono text-xs">
                Esc
              </p>
              <XIcon
                className="mr-1 size-3 stroke-muted-foreground/70 transition-all hover:cursor-pointer"
                strokeWidth={3}
                onClick={removeSelectorNode}
              />
            </span>
          </Label>
        </div>
        <Separator />
        <CommandInput
          ref={inputRef}
          className="!py-0 text-xs"
          placeholder="Start typing to search for an action..."
          onValueChange={(value) => setInputValue(value)}
          autoFocus
        />
        <CommandList className="border-b">
          <CommandEmpty>
            <span className="text-xs text-muted-foreground/70">
              No results found.
            </span>
          </CommandEmpty>
          <ActionCommandSelector nodeId={id} inputValue={inputValue} />
        </CommandList>
      </Command>
      <Handle
        type="target"
        position={targetPosition ?? Position.Top}
        isConnectable={false} // Prevent initiating a connection from the selector node
        className={cn(
          "left-1/2 !size-8 !-translate-x-1/2 !border-none !bg-transparent"
        )}
      />
    </div>
  )
})

function ActionCommandSelector({
  nodeId,
  inputValue,
}: {
  nodeId: string
  inputValue: string
}) {
  const { registryActions, registryActionsIsLoading, registryActionsError } =
    useWorkbenchRegistryActions()

  if (!registryActions || registryActionsIsLoading) {
    return (
      <ScrollArea className="h-full">
        <CommandGroup heading="Loading actions..." className="text-xs">
          {Array.from({ length: 3 }).map((_, index) => (
            <CommandItem key={index} className="text-xs">
              <div className="w-full flex-col">
                <div className="flex items-center justify-start">
                  <Skeleton className="mr-2 size-5" />
                  <Skeleton className="h-4 w-32" />
                </div>
                <Skeleton className="mt-1 h-3 w-24" />
              </div>
            </CommandItem>
          ))}
        </CommandGroup>
      </ScrollArea>
    )
  }
  if (registryActionsError) {
    console.error("Failed to load actions", registryActionsError)
    return (
      <div className="flex size-full items-center justify-center">
        <CloudOffIcon className="size-8 text-muted-foreground" />
      </div>
    )
  }

  return (
    <ScrollArea className="h-full overflow-y-auto">
      {filterActions(registryActions, inputValue).length > 0 && (
        <ActionCommandGroup
          group="Suggestions"
          nodeId={nodeId}
          registryActions={registryActions}
          inputValue={inputValue}
        />
      )}
    </ScrollArea>
  )
}

function ActionCommandGroup({
  group,
  nodeId,
  registryActions,
  inputValue,
}: {
  group: string
  nodeId: string
  registryActions: RegistryActionRead[]
  inputValue: string
}) {
  const { workspaceId, workflowId, reactFlow } = useWorkflowBuilder()
  const { getNode, setNodes, setEdges } = reactFlow

  // Move sortedActions and filterResults logic here
  const sortedActions = useMemo(() => {
    return [...registryActions].sort((a, b) => a.action.localeCompare(b.action))
  }, [registryActions])

  const filterResults = useMemo(() => {
    return filterActions(sortedActions, inputValue)
  }, [sortedActions, inputValue])

  const handleSelect = useCallback(
    async (registryAction: RegistryActionRead) => {
      if (!workflowId) {
        return
      }
      console.log("Selected action:", registryAction)
      const { position } = getNode(nodeId) as Node<SelectorNodeData>

      try {
        const type = registryAction.action
        const title = registryAction.default_title || registryAction.action
        const { id } = await actionsCreateAction({
          workspaceId,
          requestBody: {
            workflow_id: workflowId,
            type,
            title,
          },
        })
        const newNode = {
          id,
          type: "udf",
          position,
          data: {
            type,
            isConfigured: false,
          },
        } as ActionNodeType
        // Given successful creation, we can now remove the selector node
        // Find the current "selector" node and replace it with the new node
        // XXX: Actually just filter all ephemeral nodes
        // Create Action in database
        setNodes((prevNodes) =>
          prevNodes
            .filter((n) => !isEphemeral(n))
            .map((n) => ({ ...n, selected: false }))
            .concat({ ...newNode, selected: true })
        )
        // At this point, we have an edge between some other node and the selector node
        // We need to create an edge between the new node and the other node
        setEdges((prevEdges) =>
          prevEdges.map((edge) =>
            edge.target === nodeId
              ? {
                  ...edge,
                  target: newNode.id,
                }
              : edge
          )
        )
      } catch (error) {
        console.error("An error occurred while creating a new node:", error)
        toast({
          title: "Failed to create new node",
          description: "Could not create new node.",
        })
        return // Abort
      }
    },
    [getNode, nodeId, workflowId, workspaceId, setNodes, setEdges]
  )

  return (
    <CommandGroup heading={group} className="text-xs">
      {filterResults.map((result) => {
        const action = result.obj

        if (highlight) {
          const highlighted = SEARCH_KEYS.reduce(
            (acc, key, index) => {
              const currRes = result[index]
              acc[key] = currRes.highlight() || String(action[key])
              return acc
            },
            {} as Record<keyof RegistryActionRead, string>
          )

          return (
            <CommandItem
              key={action.action}
              className="text-xs"
              onSelect={async () => await handleSelect(action)}
            >
              <div className="flex-col">
                <div className="flex items-center justify-start">
                  {getIcon(action.action, {
                    className: "size-5 mr-2",
                  })}
                  <span
                    className="text-xs"
                    dangerouslySetInnerHTML={{
                      __html: highlighted.default_title,
                    }}
                  />
                </div>
                <span
                  className="text-xs text-muted-foreground"
                  dangerouslySetInnerHTML={{
                    __html: highlighted.action,
                  }}
                />
              </div>
            </CommandItem>
          )
        } else {
          return (
            <CommandItem
              key={action.action}
              className="text-xs"
              onSelect={async () => await handleSelect(action)}
            >
              <div className="flex-col">
                <div className="flex items-center justify-start">
                  {getIcon(action.action, {
                    className: "size-5 mr-2",
                  })}
                  <span className="text-xs">{action.default_title}</span>
                </div>
                <span className="text-xs text-muted-foreground">
                  {action.action}
                </span>
              </div>
            </CommandItem>
          )
        }
      })}
    </CommandGroup>
  )
}
