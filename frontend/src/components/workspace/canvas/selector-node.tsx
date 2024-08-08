import React, { useCallback, useEffect, useRef } from "react"
import { useWorkflowBuilder } from "@/providers/builder"
import { useWorkflow } from "@/providers/workflow"
import { CloudOffIcon, XIcon } from "lucide-react"
import {
  Handle,
  Node,
  NodeProps,
  Position,
  useKeyPress,
  useNodeId,
  useReactFlow,
} from "reactflow"

import { udfConfig } from "@/config/udfs"
import { UDF, useUDFs } from "@/lib/udf"
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
import {
  createNewNode,
  isEphemeral,
} from "@/components/workspace/canvas/canvas"
import { UDFNodeData } from "@/components/workspace/canvas/udf-node"
import {
  groupByDisplayGroup,
  TOP_LEVEL_GROUP,
} from "@/components/workspace/catalog/udf-catalog"

export const SelectorTypename = "selector" as const

export interface SelectorNodeData {
  type: "selector"
}
export type SelectorNodeType = Node<SelectorNodeData>

export default React.memo(function SelectorNode({
  targetPosition,
}: NodeProps<SelectorNodeData>) {
  const id = useNodeId()
  const { workflow } = useWorkflow()
  const { setNodes, setEdges } = useReactFlow()
  const escapePressed = useKeyPress("Escape")

  const removeSelectorNode = () => {
    setNodes((nodes) => nodes.filter((node) => !isEphemeral(node)))
    setEdges((edges) => edges.filter((edge) => edge.target !== id))
  }

  useEffect(() => {
    if (escapePressed) {
      removeSelectorNode()
    }
  }, [escapePressed])

  if (!workflow || !id) {
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
          className="!py-0 text-xs"
          placeholder="Start typing to search for an action..."
        />
        <CommandList className="border-b">
          <CommandEmpty>
            <span className="text-xs text-muted-foreground/70">
              No results found.
            </span>
          </CommandEmpty>
          <UDFCommandSelector nodeId={id} />
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

function UDFCommandSelector({ nodeId }: { nodeId: string }) {
  const { udfs, isLoading: udfsLoading, error } = useUDFs(udfConfig.namespaces)
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

  if (!udfs || udfsLoading) {
    return <CenteredSpinner />
  }
  if (error) {
    console.error("Failed to load UDFs", error)
    return (
      <div className="flex size-full items-center justify-center">
        <CloudOffIcon className="size-8 text-muted-foreground" />
      </div>
    )
  }

  return (
    <ScrollArea ref={scrollAreaRef} className="h-full">
      {Object.entries(groupByDisplayGroup(udfs))
        .sort(([groupA], [groupB]) => groupA.localeCompare(groupB))
        .map(([group, udfs], idx) => (
          <UDFCommandGroup
            key={`${group}-${idx}`}
            group={group === TOP_LEVEL_GROUP ? "Core" : group}
            udfs={udfs}
            nodeId={nodeId}
          />
        ))}
    </ScrollArea>
  )
}

function UDFCommandGroup({
  group,
  udfs,
  nodeId,
}: {
  group: string
  udfs: UDF[]
  nodeId: string
}) {
  const { workflowId } = useWorkflowBuilder()
  const { getNode, setNodes, setEdges } = useReactFlow()

  const handleSelect = useCallback(
    async (udf: UDF) => {
      if (!workflowId) {
        return
      }
      console.log("Selected UDF:", udf)
      const { position: currPosition } = getNode(
        nodeId
      ) as Node<SelectorNodeData>
      const nodeData = {
        type: udf.key,
        title: udf.metadata?.default_title || udf.key,
        namespace: udf.namespace,
        status: "offline",
        isConfigured: false,
        numberOfEvents: 0,
      } as UDFNodeData
      try {
        const newNode = await createNewNode(
          "udf",
          workflowId,
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
      {udfs.map((udf) => (
        <CommandItem
          key={udf.key}
          className="text-xs"
          onSelect={async () => await handleSelect(udf)}
        >
          {getIcon(udf.key, {
            className: "size-5 mr-2",
          })}
          <span className="text-xs">{udf.metadata?.default_title}</span>
        </CommandItem>
      ))}
    </CommandGroup>
  )
}
