import React, { useCallback, useEffect, useRef } from "react"
import { RegistryActionRead } from "@/client"
import { useWorkflowBuilder } from "@/providers/builder"
import { CloudOffIcon, XIcon } from "lucide-react"
import {
  Handle,
  Node,
  NodeProps,
  Position,
  useKeyPress,
  useNodeId,
} from "reactflow"

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
import { toast } from "@/components/ui/use-toast"
import { getIcon } from "@/components/icons"
import { CenteredSpinner } from "@/components/loading/spinner"
import { ActionNodeData } from "@/components/workbench/canvas/action-node"
import {
  createNewNode,
  isEphemeral,
} from "@/components/workbench/canvas/canvas"

const TOP_LEVEL_GROUP = "__TOP_LEVEL__" as const

const groupByDisplayGroup = (
  actions: RegistryActionRead[]
): Record<string, RegistryActionRead[]> => {
  const groups = {} as Record<string, RegistryActionRead[]>
  actions.forEach((action) => {
    const displayGroup = (action.display_group || TOP_LEVEL_GROUP).toString()
    if (!groups[displayGroup]) {
      groups[displayGroup] = []
    }
    groups[displayGroup].push(action)
  })
  return groups
}

export const SelectorTypename = "selector" as const

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
  const escapePressed = useKeyPress("Escape")
  const inputRef = useRef<HTMLInputElement>(null)

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

  useEffect(() => {
    if (escapePressed) {
      removeSelectorNode()
    }
  }, [escapePressed])

  if (!workflowId || !id) {
    console.error("Workflow or node ID not found")
    return null
  }

  return (
    <div>
      <Command className="h-96 w-72 rounded-lg border shadow-sm">
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
          autoFocus
        />
        <CommandList className="border-b">
          <CommandEmpty>
            <span className="text-xs text-muted-foreground/70">
              No results found.
            </span>
          </CommandEmpty>
          <ActionCommandSelector nodeId={id} />
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

function ActionCommandSelector({ nodeId }: { nodeId: string }) {
  const { registryActions, registryActionsIsLoading, registryActionsError } =
    useWorkbenchRegistryActions()
  const scrollAreaRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handleScroll = (event: Event) => {
      event.stopPropagation()
    }

    const scrollArea = scrollAreaRef.current
    if (scrollArea) {
      scrollArea.addEventListener("wheel", handleScroll)
    }

    return () => {
      if (scrollArea) {
        scrollArea.removeEventListener("wheel", handleScroll)
      }
    }
  }, [])

  if (!registryActions || registryActionsIsLoading) {
    return <CenteredSpinner />
  }
  if (registryActionsError) {
    console.error("Failed to load actions", registryActionsError)
    return (
      <div className="flex size-full items-center justify-center">
        <CloudOffIcon className="size-8 text-muted-foreground" />
      </div>
    )
  }

  const grouped = groupByDisplayGroup(registryActions)
  return (
    <ScrollArea ref={scrollAreaRef} className="h-full">
      {Object.entries(grouped)
        .sort(([groupA], [groupB]) => groupA.localeCompare(groupB))
        .map(([group, actions], idx) => (
          <ActionCommandGroup
            key={`${group}-${idx}`}
            group={group === TOP_LEVEL_GROUP ? "Core" : group}
            registryActions={actions}
            nodeId={nodeId}
          />
        ))}
    </ScrollArea>
  )
}

function ActionCommandGroup({
  group,
  registryActions: actions,
  nodeId,
}: {
  group: string
  registryActions: RegistryActionRead[]
  nodeId: string
}) {
  const { workspaceId, workflowId, reactFlow } = useWorkflowBuilder()
  const { getNode, setNodes, setEdges } = reactFlow

  const handleSelect = useCallback(
    async (action: RegistryActionRead) => {
      if (!workflowId) {
        return
      }
      console.log("Selected action:", action)
      const { position: currPosition } = getNode(
        nodeId
      ) as Node<SelectorNodeData>
      const nodeData = {
        type: action.action,
        title: action.default_title || action.action,
        namespace: action.namespace,
        status: "offline",
        isConfigured: false,
        numberOfEvents: 0,
      } as ActionNodeData
      try {
        const newNode = await createNewNode(
          "udf",
          workflowId,
          workspaceId,
          nodeData,
          currPosition
        )
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
    [getNode, nodeId]
  )
  return (
    <CommandGroup heading={group} className="text-xs">
      {actions.map((action) => (
        <CommandItem
          key={action.action}
          className="text-xs"
          onSelect={async () => await handleSelect(action)}
        >
          {getIcon(action.action, {
            className: "size-5 mr-2",
          })}
          <span className="text-xs">{action.default_title}</span>
        </CommandItem>
      ))}
    </CommandGroup>
  )
}
