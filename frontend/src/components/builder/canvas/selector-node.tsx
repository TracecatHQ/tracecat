import {
  Handle,
  type Node,
  type NodeProps,
  Position,
  useNodeId,
} from "@xyflow/react"
import { CloudOffIcon, XIcon } from "lucide-react"
import React, { useCallback, useEffect, useMemo, useRef } from "react"
import type { GraphOperation, RegistryActionReadMinimal } from "@/client"
import { isEphemeral } from "@/components/builder/canvas/canvas"
import { getIcon } from "@/components/icons"
import { LockedFeatureModal } from "@/components/locked-feature-modal"
import {
  ActionListbox,
  type ActionSearchKey,
  type ActionSearchResult,
  HighlightedText,
  useActionSearch,
} from "@/components/registry/action-listbox"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
import { toast } from "@/components/ui/use-toast"
import {
  useBuilderRegistryActions,
  useGraph,
  useGraphOperations,
} from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { useWorkflowBuilder } from "@/providers/builder"

export const SelectorTypename = "selector" as const

const SEARCH_KEYS = [
  "action",
  "default_title",
  "display_group",
] as const satisfies readonly ActionSearchKey[]
const ACTION_MATCH = SEARCH_KEYS.indexOf("action")
const TITLE_MATCH = SEARCH_KEYS.indexOf("default_title")

function isLockedAction(action: RegistryActionReadMinimal): boolean {
  return action.availability?.locked ?? false
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
      <div className="flex h-96 w-72 flex-col overflow-hidden rounded-lg border bg-popover text-popover-foreground shadow-sm">
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
        <ActionCommandSelector
          nodeId={id}
          inputValue={inputValue}
          onInputValueChange={setInputValue}
          inputRef={inputRef}
        />
      </div>
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
  onInputValueChange,
  inputRef,
}: {
  nodeId: string
  inputValue: string
  onInputValueChange: (value: string) => void
  inputRef: React.RefObject<HTMLInputElement>
}) {
  const { registryActions, registryActionsIsLoading, registryActionsError } =
    useBuilderRegistryActions({ includeLocked: true })
  const sortedActions = useMemo(() => {
    if (!registryActions) {
      return []
    }
    return [...registryActions].sort((a, b) => a.action.localeCompare(b.action))
  }, [registryActions])
  const { results } = useActionSearch(sortedActions, inputValue, SEARCH_KEYS)

  if (registryActionsError) {
    console.error("Failed to load actions", registryActionsError)
    return (
      <div className="flex size-full items-center justify-center">
        <CloudOffIcon className="size-8 text-muted-foreground" />
      </div>
    )
  }

  return (
    <ActionCommandGroup
      nodeId={nodeId}
      results={results}
      inputValue={inputValue}
      onInputValueChange={onInputValueChange}
      inputRef={inputRef}
      isLoading={!registryActions || registryActionsIsLoading}
    />
  )
}

function ActionCommandGroup({
  nodeId,
  results,
  inputValue,
  onInputValueChange,
  inputRef,
  isLoading,
}: {
  nodeId: string
  results: ActionSearchResult[]
  inputValue: string
  onInputValueChange: (value: string) => void
  inputRef: React.RefObject<HTMLInputElement>
  isLoading: boolean
}) {
  const {
    workspaceId,
    workflowId,
    reactFlow,
    setSelectedNodeId,
    actionPanelRef,
  } = useWorkflowBuilder()
  const { getNode, getEdges, setNodes, setEdges } = reactFlow
  const { data: graphData } = useGraph(workspaceId, workflowId ?? "")
  const { applyGraphOperations, refetchGraph } = useGraphOperations(
    workspaceId,
    workflowId ?? ""
  )

  const openActionPanelForNode = useCallback(
    (actionId?: string) => {
      if (!actionId) {
        return
      }

      setSelectedNodeId(actionId)
      const panelHandle = actionPanelRef.current
      if (panelHandle) {
        if (panelHandle.setOpen) {
          panelHandle.setOpen(true)
        } else {
          panelHandle.expand?.()
        }
      }
    },
    [actionPanelRef, setSelectedNodeId]
  )

  const [lockedFeatureOpen, setLockedFeatureOpen] = React.useState(false)

  const handleSelect = useCallback(
    async (registryAction: RegistryActionReadMinimal) => {
      if (isLockedAction(registryAction)) {
        setLockedFeatureOpen(true)
        return
      }
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
        const previousIds = new Set(
          (graphData?.nodes ?? []).map((n) => String(n.id))
        )
        const newNode = graphAfterAdd.nodes.find(
          (n) => !previousIds.has(String(n.id))
        )
        const newNodeId = newNode?.id as string | undefined

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
        openActionPanelForNode(newNodeId)
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
              (latestGraph.nodes ?? []).map((n) => String(n.id))
            )
            const newNode = graphAfterAdd.nodes.find(
              (n) => !previousIds.has(String(n.id))
            )
            const newNodeId = newNode?.id as string | undefined

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
            openActionPanelForNode(newNodeId)
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
      openActionPanelForNode,
    ]
  )

  return (
    <>
      <LockedFeatureModal
        open={lockedFeatureOpen}
        onOpenChange={setLockedFeatureOpen}
      />
      <ActionListbox
        results={results}
        query={inputValue}
        onQueryChange={onInputValueChange}
        inputRef={inputRef}
        autoFocus
        fill
        placeholder="Start typing to search for an action..."
        inputClassName="py-0 text-xs"
        heading="Suggestions"
        label="Actions"
        empty={
          <span className="text-muted-foreground/70">
            {isLoading ? "Loading actions..." : "No results found."}
          </span>
        }
        onSelect={(action) => void handleSelect(action)}
        renderItem={({ action, matches }) => (
          <div
            className={cn(
              "flex w-full items-center gap-3 py-2 text-xs",
              isLockedAction(action) && "text-muted-foreground"
            )}
          >
            {getIcon(action.action, {
              className: "size-8 rounded-md border p-1.5",
            })}
            <div className="flex min-w-0 flex-1 flex-col">
              <span className="truncate text-xs font-medium">
                <HighlightedText
                  result={matches[TITLE_MATCH]}
                  text={action.default_title ?? action.action}
                />
              </span>
              <span className="truncate text-xs text-muted-foreground">
                <HighlightedText
                  result={matches[ACTION_MATCH]}
                  text={action.action}
                />
              </span>
            </div>
          </div>
        )}
      />
    </>
  )
}
