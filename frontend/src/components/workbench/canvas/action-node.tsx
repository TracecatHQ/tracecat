import React, { useCallback, useEffect, useMemo, useRef, useState } from "react"
import Link from "next/link"
import { useWorkflowBuilder } from "@/providers/builder"
import {
  NodeToolbar,
  Position,
  useEdges,
  type Node,
  type NodeProps,
  type XYPosition,
} from "@xyflow/react"
import {
  AlertTriangleIcon,
  CircleCheckBigIcon,
  CopyIcon,
  LayoutListIcon,
  MessagesSquare,
  PencilIcon,
  SquareArrowOutUpRightIcon,
  Trash2Icon,
} from "lucide-react"
import { useForm } from "react-hook-form"
import YAML from "yaml"

import {
  useAction,
  useGetRegistryAction,
  useWorkflowManager,
} from "@/lib/hooks"
import { cn, isEmptyObjectOrNullish, slugify } from "@/lib/utils"
import { CHILD_WORKFLOW_ACTION_TYPE } from "@/lib/workflow"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Form, FormControl, FormField, FormItem } from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { useToast } from "@/components/ui/use-toast"
import { getIcon } from "@/components/icons"
import { AlertNotification } from "@/components/notifications"
import {
  ActionSoruceSuccessHandle,
  ActionSourceErrorHandle,
  ActionTargetHandle,
} from "@/components/workbench/canvas/custom-handle"

export type ActionNodeData = {
  id: string
  type: string // alias for key
  position: XYPosition
  data: ActionNodeData
  isConfigured: boolean

  // Allow any additional properties from legacy data
  [key: string]: unknown
}

export type ActionNodeType = Node<ActionNodeData, "udf">

export default React.memo(function ActionNode({
  selected,
  id,
}: NodeProps<ActionNodeType>) {
  const [error, setError] = useState<string | null>(null)
  const {
    workflowId,
    getNode,
    workspaceId,
    reactFlow,
    sidebarRef,
    setSelectedActionEventRef,
  } = useWorkflowBuilder()
  const { toast } = useToast()
  // SAFETY: Node only exists if it's in the workflow
  const { action, actionIsLoading, updateAction } = useAction(
    id,
    workspaceId,
    workflowId!
  )
  const { registryAction } = useGetRegistryAction(action?.type)
  const isConfigured = !isEmptyObjectOrNullish(action?.inputs)
  const [showToolbar, setShowToolbar] = useState(false)
  const nodeRef = useRef<HTMLDivElement>(null)
  const hideTimeoutRef = useRef<number>()

  // Clear timeout on unmount
  useEffect(() => {
    return () => {
      if (hideTimeoutRef.current) {
        window.clearTimeout(hideTimeoutRef.current)
      }
    }
  }, [])

  const form = useForm({
    values: {
      title: action?.title ?? registryAction?.default_title ?? "",
    },
  })

  const onSubmit = useCallback(
    async (values: { title: string }) => {
      if (!action) {
        return
      }
      try {
        await updateAction({
          title: values.title,
        })
      } catch (error) {
        console.error("Failed to update action title:", error)
        toast({
          title: "Couldn't update action title",
          description: "Please try again.",
        })
      }
    },
    [action, updateAction]
  )

  const handleDeleteNode = useCallback(async () => {
    try {
      if (!workflowId || !id) {
        throw new Error("Missing required data to delete node")
      }
      const node = getNode(id)
      if (!node) {
        console.error("Could not find node with ID", id)
        throw new Error("Could not find node to delete")
      }
      reactFlow.deleteElements({ nodes: [node] })
    } catch (error) {
      console.error("An error occurred while deleting Action nodes:", error)
      toast({
        title: "Error deleting action node",
        description: "Failed to delete action node.",
        variant: "destructive",
      })
    }
  }, [id, toast, workflowId, getNode, reactFlow])

  // Add this to track incoming edges
  const edges = useEdges()
  const incomingEdges = edges.filter((edge) => edge.target === id)
  const isChildWorkflow = action?.type === CHILD_WORKFLOW_ACTION_TYPE
  const isInteractive = useMemo(
    () => Boolean(action?.is_interactive),
    [action?.is_interactive, action]
  )

  const actionInputsObj = useMemo(() => {
    try {
      // Use YAML.parse with strict schema to catch duplicate keys
      setError(null)
      return action?.inputs
        ? YAML.parse(action.inputs, {
            schema: "core",
            strict: true,
            uniqueKeys: true,
          })
        : {}
    } catch (error) {
      const description = (
        <div className="flex flex-col space-y-2">
          <div className="flex items-center space-x-2">
            <p>
              <AlertTriangleIcon className="inline size-4 min-w-4 fill-yellow-500 stroke-white" />
              <b className="inline rounded-sm bg-muted-foreground/10 p-0.5 font-mono">
                ACTIONS.{action?.title}
              </b>
              has an invalid configuration. Please ensure that the action inputs
              are valid YAML and do not contain duplicate keys.
            </p>
          </div>
        </div>
      )
      console.error("Failed to parse action inputs:", error)
      toast({
        title: "Invalid action configuration",
        description,
      })
      setError("Invalid configuration")
      return {}
    }
  }, [action, toast])
  const childWorkflowId = actionInputsObj?.workflow_id
    ? String(actionInputsObj?.workflow_id)
    : undefined
  const childWorkflowAlias = actionInputsObj?.workflow_alias
    ? String(actionInputsObj?.workflow_alias)
    : undefined

  // Create a skeleton loading state within the card frame
  const renderContent = () => {
    if (actionIsLoading) {
      return (
        <>
          <CardHeader className="p-4">
            <div className="flex w-full items-center space-x-4">
              <Skeleton className="size-10 rounded-full" />
              <div className="flex w-full flex-1 justify-between space-x-12">
                <div className="flex flex-col space-y-2">
                  <Skeleton className="h-4 w-32" />
                  <Skeleton className="h-3 w-24" />
                </div>
                <Skeleton className="size-6" />
              </div>
            </div>
          </CardHeader>
          <Separator />
          <CardContent className="p-4 py-2">
            <div className="grid grid-cols-2 space-x-4 text-xs text-muted-foreground">
              <div className="flex items-center space-x-2">
                <Skeleton className="size-4" />
                <Skeleton className="h-3 w-16" />
              </div>
            </div>
          </CardContent>
        </>
      )
    }

    if (!action) {
      return (
        <div className="p-4">
          <AlertNotification
            variant="warning"
            title="Could not load action"
            message="Please try again."
          />
        </div>
      )
    }

    return (
      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)}>
          <CardHeader
            className="p-4"
            onBlur={() => {
              form.handleSubmit(onSubmit)()
            }}
          >
            <div className="flex w-full items-center space-x-4">
              {getIcon(action.type, {
                className: "size-10 p-2",
              })}

              <div
                id="ASDF"
                className="flex w-full flex-1 justify-between space-x-12"
              >
                <div className="flex flex-col">
                  <CardTitle className="flex items-center justify-start space-x-2 text-xs font-medium leading-none">
                    <FormField
                      control={form.control}
                      name="title"
                      render={({ field }) => (
                        <FormItem className="!w-auto">
                          <FormControl className="!w-auto">
                            <div className="!w-auto">
                              <Input
                                type="text"
                                {...field}
                                className={cn(
                                  "m-0 h-5 shrink-0 border-none bg-transparent p-0",
                                  "text-xs font-medium leading-none",
                                  "shadow-none outline-none",
                                  "hover:cursor-pointer hover:bg-muted-foreground/10",
                                  "focus:ring-0 focus:ring-offset-0",
                                  "focus-visible:bg-muted-foreground/10 focus-visible:ring-0 focus-visible:ring-offset-0"
                                )}
                              />
                            </div>
                          </FormControl>
                        </FormItem>
                      )}
                    />
                  </CardTitle>
                  <CardDescription className="mt-2 text-xs text-muted-foreground">
                    {action.type}
                  </CardDescription>
                </div>
              </div>
            </div>
          </CardHeader>
          <Separator />
          <CardContent className="p-4 py-2">
            <div className="grid grid-cols-2 space-x-4 text-xs text-muted-foreground">
              <div className="flex items-center space-x-2">
                {error ? (
                  <div className="flex items-center space-x-1">
                    <AlertTriangleIcon className="size-4 fill-yellow-500 stroke-white" />
                    <span className="text-xs capitalize">{error}</span>
                  </div>
                ) : (
                  <div className="flex items-center space-x-1">
                    {isConfigured ? (
                      <CircleCheckBigIcon className="size-4 text-emerald-500" />
                    ) : (
                      <LayoutListIcon className="size-4 text-gray-400" />
                    )}
                    <span className="text-xs capitalize">
                      {isConfigured ? "Ready" : "Missing inputs"}
                    </span>
                  </div>
                )}
              </div>
              {isChildWorkflow && (
                <ChildWorkflowLink
                  workspaceId={workspaceId}
                  childWorkflowId={childWorkflowId}
                  childWorkflowAlias={childWorkflowAlias}
                />
              )}
              {isInteractive && (
                <div className="flex justify-end">
                  <Badge
                    variant="secondary"
                    className="bg-sky-300/30 text-foreground/60 hover:cursor-pointer hover:bg-muted-foreground/5"
                  >
                    <MessagesSquare
                      className="mr-2 size-3 text-foreground/60"
                      strokeWidth={3}
                    />
                    <span>Interactive</span>
                  </Badge>
                </div>
              )}
            </div>
          </CardContent>
        </form>
      </Form>
    )
  }

  const handleMouseEnter = useCallback(() => {
    if (hideTimeoutRef.current) {
      window.clearTimeout(hideTimeoutRef.current)
    }
    setShowToolbar(true)
  }, [])

  const handleMouseLeave = useCallback(() => {
    // Use a small delay to allow the mouse to move between elements
    hideTimeoutRef.current = window.setTimeout(() => {
      setShowToolbar(false)
    }, 100)
  }, [])

  if (!action) {
    return null
  }

  return (
    <Card
      ref={nodeRef}
      className={cn("min-w-72", selected && "shadow-xl drop-shadow-xl")}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      {renderContent()}
      <ActionTargetHandle
        join_strategy={action?.control_flow?.join_strategy}
        indegree={incomingEdges.length}
      />
      <ActionSoruceSuccessHandle type="source" />
      <ActionSourceErrorHandle type="source" />
      <NodeToolbar
        isVisible={showToolbar}
        position={Position.Right}
        align="start"
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
        className={cn(
          `
            [&>button]:variant-ghost
            flex min-w-16 flex-col
            rounded-lg border bg-background p-0.5 shadow-md
            [&>button]:h-7
            [&>button]:justify-start
            [&>button]:px-2
            [&>button]:py-1
            [&>button]:text-foreground/70
            [&>button_span]:text-[0.75rem]
            [&>button_svg]:mr-1
            [&>button_svg]:size-3
          `,
          selected && "shadow-xl drop-shadow-xl"
        )}
      >
        <Button
          variant="ghost"
          onClick={(e) => {
            e.stopPropagation()
            navigator.clipboard.writeText(
              `ACTIONS.${slugify(action.title)}.result`
            )
            toast({
              title: "Copied action reference",
              description: (
                <Badge
                  variant="secondary"
                  className="bg-muted-foreground/10 font-mono text-xs font-normal tracking-tight"
                >
                  {`ACTIONS.${slugify(action.title)}.result`}
                </Badge>
              ),
            })
          }}
        >
          <CopyIcon />
          <span className="text-xs">Copy Reference</span>
        </Button>
        <Button
          variant="ghost"
          onClick={(e) => {
            e.stopPropagation()
            form.setFocus("title")
          }}
        >
          <PencilIcon />
          <span className="text-xs">Rename</span>
        </Button>
        <Button
          variant="ghost"
          onClick={(e) => {
            e.stopPropagation()
            sidebarRef.current?.setOpen(true)
            sidebarRef.current?.setActiveTab("action-input")
            setSelectedActionEventRef(slugify(action.title))
          }}
        >
          <LayoutListIcon />
          <span className="text-xs">View Last Input</span>
        </Button>
        <Button
          variant="ghost"
          onClick={(e) => {
            e.stopPropagation()
            sidebarRef.current?.setOpen(true)
            sidebarRef.current?.setActiveTab("action-result")
            setSelectedActionEventRef(slugify(action.title))
          }}
        >
          <CircleCheckBigIcon />
          <span className="text-xs">View Last Result</span>
        </Button>
        {action?.is_interactive && (
          <Button
            variant="ghost"
            onClick={(e) => {
              e.stopPropagation()
              sidebarRef.current?.setOpen(true)
              sidebarRef.current?.setActiveTab("action-interaction")
              setSelectedActionEventRef(slugify(action.title))
            }}
          >
            <MessagesSquare />
            <span className="text-xs">View Last Interaction</span>
          </Button>
        )}
        <Button variant="ghost" onClick={handleDeleteNode}>
          <Trash2Icon className="size-3 text-red-600" />
          <span className="text-xs text-red-600">Delete</span>
        </Button>
      </NodeToolbar>
    </Card>
  )
})

function ChildWorkflowLink({
  workspaceId,
  childWorkflowId,
  childWorkflowAlias,
}: {
  workspaceId: string
  childWorkflowId?: string
  childWorkflowAlias?: string
}) {
  const { workflows } = useWorkflowManager()
  const { setSelectedNodeId } = useWorkflowBuilder()
  const childIdFromAlias = workflows?.find(
    (w) => w.alias === childWorkflowAlias
  )?.id

  const handleClearSelection = () => {
    setSelectedNodeId(null)
  }

  const inner = () => {
    if (childWorkflowId) {
      return (
        <Link
          href={`/workspaces/${workspaceId}/workflows/${childWorkflowId}`}
          onClick={handleClearSelection}
        >
          <div className="flex items-center gap-1">
            <span className="font-normal">Open workflow</span>
            <SquareArrowOutUpRightIcon className="size-3" />
          </div>
        </Link>
      )
    }
    if (childWorkflowAlias) {
      if (!childIdFromAlias) {
        return (
          <div className="flex items-center gap-1">
            <span className="font-normal">Cannot find workflow by alias</span>
            <AlertTriangleIcon className="size-3 text-red-500" />
          </div>
        )
      }
      return (
        <div className="flex items-center gap-1">
          <Link
            href={`/workspaces/${workspaceId}/workflows/${childIdFromAlias}`}
            onClick={handleClearSelection}
          >
            <div className="flex items-center gap-1">
              <span className="font-mono font-normal tracking-tighter text-foreground/80">
                {childWorkflowAlias}
              </span>
              <SquareArrowOutUpRightIcon className="size-3" />
            </div>
          </Link>
        </div>
      )
    }
    return <span className="font-normal">Missing identifier</span>
  }
  return (
    <div className="flex justify-end">
      <Badge
        variant="outline"
        className="text-foreground/70 hover:cursor-pointer hover:bg-muted-foreground/5"
      >
        {inner()}
      </Badge>
    </div>
  )
}
