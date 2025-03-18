import React, { useCallback, useMemo, useState } from "react"
import Link from "next/link"
import { useWorkflowBuilder } from "@/providers/builder"
import {
  useEdges,
  type Node,
  type NodeProps,
  type XYPosition,
} from "@xyflow/react"
import {
  AlertTriangleIcon,
  ChevronDownIcon,
  CircleCheckBigIcon,
  LayoutListIcon,
  MessagesSquare,
  SquareArrowOutUpRightIcon,
  Trash2Icon,
} from "lucide-react"
import { useForm } from "react-hook-form"
import YAML from "yaml"

import { useAction, useGetRegistryAction } from "@/lib/hooks"
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Form, FormControl, FormField, FormItem } from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { useToast } from "@/components/ui/use-toast"
import { CopyButton } from "@/components/copy-button"
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

  const form = useForm({
    values: {
      title: action?.title ?? registryAction?.default_title ?? "",
    },
  })

  const onSubmit = useCallback(
    (values: { title: string }) => {
      if (!action) {
        return
      }
      updateAction({
        title: values.title,
      })
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
      <>
        <CardHeader className="p-4">
          <div className="flex w-full items-center space-x-4">
            {getIcon(action.type, {
              className: "size-10 p-2",
            })}

            <div className="flex w-full flex-1 justify-between space-x-12">
              <div className="flex flex-col">
                <CardTitle className="flex w-full items-center space-x-2 text-xs font-medium leading-none">
                  <Form {...form}>
                    <form onSubmit={form.handleSubmit(onSubmit)}>
                      <FormField
                        control={form.control}
                        name="title"
                        render={({ field }) => (
                          <FormItem>
                            <FormControl>
                              <Input
                                type="text"
                                {...field}
                                onBlur={(e) => {
                                  field.onBlur()
                                  form.handleSubmit(onSubmit)()
                                }}
                                className="w-full border-none bg-transparent py-0 text-xs font-medium leading-none shadow-none hover:cursor-pointer focus:ring-0 focus:ring-offset-0"
                              />
                            </FormControl>
                          </FormItem>
                        )}
                      />
                    </form>
                  </Form>
                  <CopyButton
                    value={`ACTIONS.${slugify(action.title)}.result`}
                    toastMessage="Copied action reference to clipboard"
                    tooltipMessage="Copy action reference"
                  />
                </CardTitle>
                <CardDescription className="mt-2 text-xs text-muted-foreground">
                  {action.type}
                </CardDescription>
              </div>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline" className="m-0 size-6 p-0">
                    <ChevronDownIcon className="m-1 size-4 text-muted-foreground" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent>
                  <DropdownMenuItem
                    onClick={(e) => {
                      e.stopPropagation()
                      sidebarRef.current?.setActiveTab("action-input")
                      setSelectedActionEventRef(slugify(action.title))
                    }}
                  >
                    <LayoutListIcon className="mr-2 size-4" />
                    <span className="text-xs">View Last Input</span>
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onClick={(e) => {
                      e.stopPropagation()
                      sidebarRef.current?.setActiveTab("action-result")
                      setSelectedActionEventRef(slugify(action.title))
                    }}
                  >
                    <CircleCheckBigIcon className="mr-2 size-4" />
                    <span className="text-xs">View Last Result</span>
                  </DropdownMenuItem>
                  {action?.is_interactive && (
                    <DropdownMenuItem
                      onClick={(e) => {
                        e.stopPropagation()
                        sidebarRef.current?.setActiveTab("action-interaction")
                        setSelectedActionEventRef(slugify(action.title))
                      }}
                    >
                      <MessagesSquare className="mr-2 size-4" />
                      <span className="text-xs">View Last Interaction</span>
                    </DropdownMenuItem>
                  )}
                  <DropdownMenuItem onClick={handleDeleteNode}>
                    <Trash2Icon className="mr-2 size-4 text-red-600" />
                    <span className="text-xs text-red-600">Delete</span>
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
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
      </>
    )
  }

  return (
    <Card className={cn("min-w-72", selected && "shadow-xl drop-shadow-xl")}>
      {renderContent()}
      <ActionTargetHandle
        join_strategy={action?.control_flow?.join_strategy}
        indegree={incomingEdges.length}
      />
      <ActionSoruceSuccessHandle type="source" />
      <ActionSourceErrorHandle type="source" />
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
