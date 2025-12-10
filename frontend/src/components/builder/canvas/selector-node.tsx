import {
  Handle,
  type Node,
  type NodeProps,
  Position,
  useNodeId,
} from "@xyflow/react"
import fuzzysort from "fuzzysort"
import { CloudOffIcon, XIcon } from "lucide-react"
import React, { useCallback, useEffect, useMemo, useRef } from "react"
import type { GraphOperation, RegistryActionReadMinimal } from "@/client"
import { isEphemeral } from "@/components/builder/canvas/canvas"
import { getIcon } from "@/components/icons"
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
import {
  useBuilderRegistryActions,
  useGraph,
  useGraphOperations,
} from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { useWorkflowBuilder } from "@/providers/builder"

export const SelectorTypename = "selector" as const

const highlight = true
const SEARCH_KEYS = [
  "action",
  "default_title",
  "display_group",
] as (keyof RegistryActionReadMinimal)[]

function filterActions(actions: RegistryActionReadMinimal[], search: string) {
  const results = fuzzysort.go<RegistryActionReadMinimal>(search, actions, {
    all: true,
    keys: SEARCH_KEYS,
  })
  return results
}

export type SelectorNodeData = {
  type: "selector"
}
export type SelectorNodeType = Node<SelectorNodeData, "selector">

export default React.memo(function SelectorNode({
  targetPosition,
}: NodeProps<SelectorNodeType>) {
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
          onValueChange={(value) => {
            // First update the value
            setInputValue(value)
            // Then force scroll to top of the list
            requestAnimationFrame(() => {
              const commandList = document.querySelector("[cmdk-list]")
              if (commandList) {
                commandList.scrollTop = 0
              }
            })
          }}
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
        isConnectable={false}
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
    useBuilderRegistryActions()

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
  registryActions: RegistryActionReadMinimal[]
  inputValue: string
}) {
  const { workspaceId, workflowId, reactFlow } = useWorkflowBuilder()
  const { getNode, getEdges, setNodes, setEdges } = reactFlow
  const { data: graphData } = useGraph(workspaceId, workflowId ?? "")
  const { applyGraphOperations, refetchGraph } = useGraphOperations(
    workspaceId,
    workflowId ?? ""
  )

  // Move sortedActions and filterResults logic here
  const sortedActions = useMemo(() => {
    return [...registryActions].sort((a, b) => a.action.localeCompare(b.action))
  }, [registryActions])

  const filterResults = useMemo(() => {
    return filterActions(sortedActions, inputValue)
  }, [sortedActions, inputValue])

  const handleSelect = useCallback(
    async (registryAction: RegistryActionReadMinimal) => {
      if (!workflowId) {
        return
      }
      console.log("Selected action:", registryAction)
      const { position } = getNode(nodeId) as Node<SelectorNodeData>

      // Find any incoming edge to the selector node before we modify state
      const currentEdges = getEdges()
      const incomingEdge = currentEdges.find((e) => e.target === nodeId)

      const type = registryAction.action
      const title = registryAction.default_title || registryAction.action

      try {
        const addNodeOp: GraphOperation = {
          type: "add_node",
          payload: {
            type,
            title,
            position_x: position.x,
            position_y: position.y,
          },
        }

        // Step 1: create node
        const graphAfterAdd = await applyGraphOperations({
          baseVersion: graphData?.version ?? 1,
          operations: [addNodeOp],
        })

        // Identify the new node by diffing ids
        const previousIds = new Set((graphData?.nodes ?? []).map((n) => n.id))
        const newNode = graphAfterAdd.nodes.find((n) => !previousIds.has(n.id))
        const newNodeId = newNode?.id

        // Step 2: connect incoming edge to the new node
        if (incomingEdge && newNodeId) {
          const isTrigger = incomingEdge.source.startsWith("trigger")
          const addEdgeOp: GraphOperation = {
            type: "add_edge",
            payload: {
              source_id: incomingEdge.source,
              source_type: isTrigger ? "trigger" : "udf",
              target_id: newNodeId,
              source_handle: isTrigger
                ? undefined
                : ((incomingEdge.sourceHandle as "success" | "error") ??
                  "success"),
            },
          }

          await applyGraphOperations({
            baseVersion: graphAfterAdd.version,
            operations: [addEdgeOp],
          })
        }

        // Let the canvas react to graph cache updates; just remove ephemeral selector locally
        setNodes((prevNodes) => prevNodes.filter((n) => !isEphemeral(n)))
        setEdges((prevEdges) =>
          prevEdges.filter((edge) => edge.target !== nodeId)
        )
      } catch (error) {
        const apiError = error as { status?: number }
        if (apiError.status === 409) {
          console.log("Version conflict, refetching graph and retrying...")
          try {
            const latestGraph = await refetchGraph()
            const addNodeOp: GraphOperation = {
              type: "add_node",
              payload: {
                type,
                title,
                position_x: position.x,
                position_y: position.y,
              },
            }
            const graphAfterAdd = await applyGraphOperations({
              baseVersion: latestGraph.version,
              operations: [addNodeOp],
            })

            const previousIds = new Set(
              (latestGraph.nodes ?? []).map((n) => n.id)
            )
            const newNode = graphAfterAdd.nodes.find(
              (n) => !previousIds.has(n.id)
            )
            const newNodeId = newNode?.id

            if (incomingEdge && newNodeId) {
              const isTrigger = incomingEdge.source.startsWith("trigger")
              const addEdgeOp: GraphOperation = {
                type: "add_edge",
                payload: {
                  source_id: incomingEdge.source,
                  source_type: isTrigger ? "trigger" : "udf",
                  target_id: newNodeId,
                  source_handle: isTrigger
                    ? undefined
                    : ((incomingEdge.sourceHandle as "success" | "error") ??
                      "success"),
                },
              }

              await applyGraphOperations({
                baseVersion: graphAfterAdd.version,
                operations: [addEdgeOp],
              })
            }

            setNodes((prevNodes) => prevNodes.filter((n) => !isEphemeral(n)))
            setEdges((prevEdges) =>
              prevEdges.filter((edge) => edge.target !== nodeId)
            )
          } catch (retryError) {
            console.error("Failed to persist node after retry:", retryError)
            toast({
              title: "Failed to create new node",
              description: "Could not create new node after retry.",
            })
          }
        } else {
          console.error("An error occurred while creating a new node:", error)
          toast({
            title: "Failed to create new node",
            description: "Could not create new node.",
          })
        }
      }
    },
    [
      getNode,
      getEdges,
      nodeId,
      workflowId,
      workspaceId,
      setNodes,
      setEdges,
      applyGraphOperations,
      refetchGraph,
      graphData?.version,
    ]
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
            {} as Record<keyof RegistryActionReadMinimal, string>
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
