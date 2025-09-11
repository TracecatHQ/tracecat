import { QuestionMarkIcon } from "@radix-ui/react-icons"
import {
  type Node,
  type NodeProps,
  NodeToolbar,
  Position,
  useEdges,
  type XYPosition,
} from "@xyflow/react"
import {
  AlertTriangleIcon,
  CircleCheckBigIcon,
  CircleHelp,
  CopyIcon,
  CopyPlusIcon,
  LayoutListIcon,
  MessagesSquare,
  PencilIcon,
  SquareArrowOutUpRightIcon,
  Trash2Icon,
} from "lucide-react"
import Link from "next/link"
import React, {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import { FormProvider, useForm, useFormContext } from "react-hook-form"
import YAML from "yaml"
import {
  type ActionRead,
  actionsCreateAction,
  type ValidationResult,
} from "@/client"
import {
  ActionSoruceSuccessHandle,
  ActionSourceErrorHandle,
  ActionTargetHandle,
} from "@/components/builder/canvas/custom-handle"
import { nodeStyles } from "@/components/builder/canvas/node-styles"
import type { EventsSidebarRef } from "@/components/builder/events/events-sidebar"
import { getIcon } from "@/components/icons"
import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Command,
  CommandGroup,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command"
import { FormControl, FormField, FormItem } from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast, useToast } from "@/components/ui/use-toast"
import { useActionNodeZoomBreakpoint } from "@/hooks/canvas"
import {
  useAction,
  useGetRegistryAction,
  useWorkflowManager,
} from "@/lib/hooks"
import { cn, slugify } from "@/lib/utils"
import { CHILD_WORKFLOW_ACTION_TYPE } from "@/lib/workflow"
import { useWorkflowBuilder } from "@/providers/builder"
import { useWorkflow } from "@/providers/workflow"

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

type ChildWorkflowInfo = {
  isChildWorkflow: boolean
  childWorkflowId?: string
  childWorkflowAlias?: string
  childIdFromAlias?: string
}

export default React.memo(function ActionNode({
  selected,
  id,
}: NodeProps<ActionNodeType>) {
  const [error, setError] = useState<ActionConfigError | null>(null)
  const {
    workflowId,
    getNode,
    workspaceId,
    reactFlow,
    sidebarRef,
    actionPanelRef,
    setSelectedActionEventRef,
  } = useWorkflowBuilder()
  const { validationErrors } = useWorkflow()

  const { toast } = useToast()
  // SAFETY: Node only exists if it's in the workflow
  const { action, actionIsLoading, updateAction } = useAction(
    id,
    workspaceId,
    workflowId
  )
  const actionValidationErrors = useMemo(() => {
    return (
      validationErrors?.filter((e) => e.ref === slugify(action?.title ?? "")) ??
      []
    )
  }, [validationErrors, action])
  const { registryAction } = useGetRegistryAction(action?.type)
  const [showToolbar, setShowToolbar] = useState(false)
  const [isMouseOverNode, setIsMouseOverNode] = useState(false)
  const [isMouseOverToolbar, setIsMouseOverToolbar] = useState(false)
  const nodeRef = useRef<HTMLDivElement>(null)
  const hideTimeoutRef = useRef<number>()
  const { breakpoint, style } = useActionNodeZoomBreakpoint()
  // Clear timeout on unmount
  const expandActionPanel = useCallback(() => {
    if (actionPanelRef.current?.isCollapsed()) {
      actionPanelRef.current?.expand()
    }
  }, [actionPanelRef])

  useEffect(() => {
    return () => {
      if (hideTimeoutRef.current) {
        window.clearTimeout(hideTimeoutRef.current)
      }
    }
  }, [])

  // Handle combined mouse over state
  useEffect(() => {
    if (isMouseOverNode || isMouseOverToolbar || selected) {
      // Mouse is over either element or node is selected, show toolbar
      if (hideTimeoutRef.current) {
        window.clearTimeout(hideTimeoutRef.current)
        hideTimeoutRef.current = undefined
      }
      setShowToolbar(true)
    } else {
      // Mouse is not over either element and node is not selected, hide toolbar after a delay
      hideTimeoutRef.current = window.setTimeout(() => {
        setShowToolbar(false)
        hideTimeoutRef.current = undefined
      }, 50)
    }

    return () => {
      if (hideTimeoutRef.current) {
        window.clearTimeout(hideTimeoutRef.current)
      }
    }
  }, [isMouseOverNode, isMouseOverToolbar, selected])

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

  const handleDuplicateNode = useCallback(async () => {
    try {
      if (!workflowId || !id || !action) {
        console.error("Missing required data to duplicate node")
        return
      }

      const originalNode = getNode(id)
      if (!originalNode) {
        console.error("Could not find node with ID", id)
        return
      }

      // Create a new node based on the current one with position offset
      const { id: newId } = await actionsCreateAction({
        workspaceId,
        requestBody: {
          workflow_id: workflowId,
          ...action,
          title: `Copy of ${action.title}`,
        },
      })
      const newNode = {
        ...originalNode,
        id: newId,
        position: {
          x: originalNode.position.x + 50,
          y: originalNode.position.y + 50,
        },
        data: originalNode.data,
        selected: false,
      }

      // Add the new node to the flow
      reactFlow.addNodes(newNode)

      toast({
        title: "Node duplicated",
        description: "The action node was duplicated successfully.",
      })
    } catch (error) {
      console.error("An error occurred while duplicating Action node:", error)
      toast({
        title: "Error duplicating action node",
        description: `Failed to duplicate action node. ${error}`,
        variant: "destructive",
      })
    }
  }, [id, action, toast, workflowId, getNode, reactFlow])

  // Add this to track incoming edges
  const edges = useEdges()
  const incomingEdges = edges.filter((edge) => edge.target === id)

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
      setError({
        type: "configuration",
        message: "Invalid configuration",
      })
      return {}
    }
  }, [action, toast])

  const Icon = useMemo(
    () =>
      getIcon(action?.type ?? "", {
        className: "size-10 p-2",
      }),
    [action?.type]
  )

  const handleNodeMouseEnter = useCallback(() => {
    setIsMouseOverNode(true)
  }, [])

  const handleNodeMouseLeave = useCallback(() => {
    setIsMouseOverNode(false)
  }, [])

  const isChildWorkflow = action?.type === CHILD_WORKFLOW_ACTION_TYPE
  const childWorkflowId = actionInputsObj?.workflow_id
    ? String(actionInputsObj?.workflow_id)
    : undefined
  const childWorkflowAlias = actionInputsObj?.workflow_alias
    ? String(actionInputsObj?.workflow_alias)
    : undefined
  const { workflows } = useWorkflowManager()
  const childIdFromAlias = useMemo(
    () => workflows?.find((w) => w.alias === childWorkflowAlias)?.id,
    [workflows, childWorkflowAlias]
  )

  const childWorkflowInfo: ChildWorkflowInfo = {
    isChildWorkflow,
    childIdFromAlias,
    childWorkflowAlias,
    childWorkflowId,
  }

  return (
    <TooltipProvider>
      <FormProvider {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)}>
          <Card
            ref={nodeRef}
            className={cn(
              "min-w-64",
              nodeStyles.common,
              selected ? nodeStyles.selected : nodeStyles.hover
            )}
            onMouseEnter={handleNodeMouseEnter}
            onMouseLeave={handleNodeMouseLeave}
            onClick={expandActionPanel}
          >
            <ActionNodeContent
              workspaceId={workspaceId}
              actionType={action?.type}
              actionInputs={actionInputsObj}
              actionIsLoading={actionIsLoading}
              actionIsInteractive={action?.is_interactive}
              submitHandler={form.handleSubmit(onSubmit)}
              style={style}
              breakpoint={breakpoint}
              Icon={Icon}
              childWorkflowInfo={childWorkflowInfo}
              error={error}
              validationErrors={actionValidationErrors}
            />
            <ActionTargetHandle
              action={action}
              indegree={incomingEdges.length}
            />
            <ActionSoruceSuccessHandle type="source" />
            <ActionSourceErrorHandle type="source" />
            {action && (
              <ActionNodeToolbar
                action={action}
                childWorkflowInfo={childWorkflowInfo}
                showToolbar={showToolbar}
                selected={selected}
                sidebarRef={sidebarRef}
                setSelectedActionEventRef={setSelectedActionEventRef}
                setIsMouseOverToolbar={setIsMouseOverToolbar}
                handleDeleteNode={handleDeleteNode}
                handleDuplicateNode={handleDuplicateNode}
              />
            )}
          </Card>
        </form>
      </FormProvider>
    </TooltipProvider>
  )
})

interface ActionConfigError {
  type: "configuration"
  message: string
}

function ActionNodeContent({
  actionType,
  actionIsLoading,
  submitHandler,
  style,
  breakpoint,
  Icon,
  workspaceId,
  childWorkflowInfo,
  error,
  validationErrors,
}: {
  actionType?: string
  actionInputs?: Record<string, unknown>
  actionIsLoading: boolean
  actionIsInteractive?: boolean
  submitHandler: () => void
  style: { fontSize: string; showContent: boolean }
  breakpoint: "small" | "large" | "medium"
  Icon: React.ReactNode
  workspaceId: string
  childWorkflowInfo: ChildWorkflowInfo
  error: ActionConfigError | null
  validationErrors: ValidationResult[]
}) {
  const form = useFormContext()

  if (actionIsLoading) {
    return (
      <CardHeader className="p-4">
        <div className="flex w-full items-center space-x-4">
          <div className="flex size-10 items-center justify-center rounded-full">
            <Skeleton className="size-10 rounded-full" />
          </div>
          <div className="flex w-full flex-1 justify-between">
            <div className="flex flex-col space-y-1">
              <Skeleton className="h-4 w-32" />
              <Skeleton className="h-3 w-24" />
            </div>
          </div>
        </div>
      </CardHeader>
    )
  }

  if (!actionType) {
    return (
      <CardHeader className="p-4">
        <div className="flex w-full items-center space-x-4">
          <div className="flex size-10 items-center justify-center rounded-full bg-muted-foreground/10">
            <QuestionMarkIcon className="size-6 text-muted-foreground" />
          </div>
          <div className="flex w-full flex-1 justify-between">
            <div className="flex flex-col space-y-1">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Unknown action
              </CardTitle>
              <CardDescription className="text-xs">
                Action type not found
              </CardDescription>
            </div>
          </div>
        </div>
      </CardHeader>
    )
  }
  return (
    <CardHeader className="p-4" onBlur={submitHandler}>
      <div className="flex w-full items-center space-x-4">
        {/* Icon */}
        {Icon}
        <div className="flex w-full flex-1 justify-between">
          <div className="flex flex-col space-y-1">
            {/* Title */}
            <CardTitle className="flex items-center justify-start space-x-2 font-medium leading-none">
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
                            "font-medium leading-none",
                            "shadow-none outline-none",
                            "hover:cursor-pointer hover:bg-muted-foreground/10",
                            "focus:ring-0 focus:ring-offset-0",
                            "focus-visible:bg-muted-foreground/10 focus-visible:ring-0 focus-visible:ring-offset-0",
                            "overflow-hidden text-ellipsis transition-all",
                            style.fontSize,
                            breakpoint === "large" && "h-7"
                          )}
                        />
                      </div>
                    </FormControl>
                  </FormItem>
                )}
              />
            </CardTitle>
            {/* Action type */}
            {style.showContent && (
              <CardDescription className="mt-2 text-xs text-muted-foreground">
                {actionType}
              </CardDescription>
            )}
          </div>
          {/* Child workflow */}

          <div className="flex items-start gap-1">
            {childWorkflowInfo.isChildWorkflow && (
              <ChildWorkflowLink
                workspaceId={workspaceId}
                childWorkflowInfo={childWorkflowInfo}
              />
            )}
          </div>
        </div>
      </div>
      {error ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <div className="absolute -right-2 -top-4 z-10">
              <AlertTriangleIcon className="size-5 bg-transparent fill-yellow-200 stroke-yellow-500 drop-shadow-md" />
            </div>
          </TooltipTrigger>
          <TooltipContent side="top" align="center" sideOffset={5}>
            <p>{error.message}</p>
          </TooltipContent>
        </Tooltip>
      ) : null}
      {validationErrors.length > 0 ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <div className="absolute -right-1.5 -top-3.5 z-10 flex items-center">
              {validationErrors.length > 0 && (
                <div className="hover:cursor-pointer">
                  <span
                    className="ml-1 flex size-5 items-center justify-center rounded-full border border-rose-300 bg-rose-400 text-xs font-bold text-white shadow"
                    aria-label={`Found ${validationErrors.length} validation errors`}
                  >
                    {validationErrors.length}
                  </span>
                </div>
              )}
            </div>
          </TooltipTrigger>
          <TooltipContent side="top" align="center" sideOffset={5}>
            <p>Found {validationErrors.length} validation errors.</p>
          </TooltipContent>
        </Tooltip>
      ) : null}
    </CardHeader>
  )
}

const COMMAND_VALUE_UNSET = "__UNSET__"

function ActionNodeToolbar({
  action,
  childWorkflowInfo,
  showToolbar,
  selected,
  sidebarRef,
  setSelectedActionEventRef,
  setIsMouseOverToolbar,
  handleDeleteNode,
  handleDuplicateNode,
}: {
  action: ActionRead
  childWorkflowInfo: ChildWorkflowInfo
  showToolbar: boolean
  selected: boolean
  sidebarRef: React.RefObject<EventsSidebarRef>
  setSelectedActionEventRef: (ref: string) => void
  setIsMouseOverToolbar: (isMouseOver: boolean) => void
  handleDeleteNode: () => void
  handleDuplicateNode: () => void
}) {
  const form = useFormContext()
  const { workspaceId } = useWorkflowBuilder()
  const {
    isChildWorkflow,
    childWorkflowAlias,
    childIdFromAlias,
    childWorkflowId,
  } = childWorkflowInfo
  const [commandValue, setCommandValue] = useState<string>(COMMAND_VALUE_UNSET)
  const handleToolbarMouseEnter = useCallback(() => {
    setIsMouseOverToolbar(true)
  }, [])

  const handleToolbarMouseLeave = useCallback(() => {
    setIsMouseOverToolbar(false)
    setCommandValue(COMMAND_VALUE_UNSET)
  }, [])

  return (
    <NodeToolbar
      isVisible={showToolbar || selected}
      position={Position.Right}
      align="start"
      onMouseEnter={handleToolbarMouseEnter}
      onMouseLeave={handleToolbarMouseLeave}
      onClick={(e) => e.stopPropagation()}
    >
      <Command
        value={commandValue}
        onValueChange={setCommandValue}
        className="min-w-[100px] rounded-lg border text-sm shadow-md [&_[cmdk-item]]:text-foreground/90 [&_[cmdk-item]]:hover:cursor-pointer"
        defaultValue={COMMAND_VALUE_UNSET}
      >
        <CommandList>
          {/* Actions */}
          <CommandGroup>
            <CommandItem
              onSelect={() => {
                const value = `ACTIONS.${slugify(action.title)}.result`
                navigator.clipboard.writeText(value)
                toast({
                  title: "Copied action reference",
                  description: (
                    <Badge
                      variant="secondary"
                      className="bg-muted-foreground/10 font-mono text-xs font-normal tracking-tight"
                    >
                      {value}
                    </Badge>
                  ),
                })
              }}
            >
              <CopyIcon className="mr-2 size-3" />
              <span>Copy reference</span>
            </CommandItem>
            <CommandItem onSelect={() => form.setFocus("title")}>
              <PencilIcon className="mr-2 size-3" />
              <span>Rename</span>
            </CommandItem>
            <CommandItem onSelect={handleDuplicateNode}>
              <CopyPlusIcon className="mr-2 size-3" />
              <span>Duplicate</span>
            </CommandItem>
            <CommandItem
              onSelect={() => {
                sidebarRef.current?.setOpen(true)
                sidebarRef.current?.setActiveTab("action-input")
                setSelectedActionEventRef(slugify(action.title))
              }}
            >
              <LayoutListIcon className="mr-2 size-3" />
              <span>Last input</span>
            </CommandItem>
            <CommandItem
              onSelect={() => {
                sidebarRef.current?.setOpen(true)
                sidebarRef.current?.setActiveTab("action-result")
                setSelectedActionEventRef(slugify(action.title))
              }}
            >
              <CircleCheckBigIcon className="mr-2 size-3" />
              <span>Last result</span>
            </CommandItem>
            {action?.is_interactive && (
              <CommandItem
                onSelect={() => {
                  sidebarRef.current?.setOpen(true)
                  sidebarRef.current?.setActiveTab("action-interaction")
                  setSelectedActionEventRef(slugify(action.title))
                }}
              >
                <MessagesSquare className="mr-2 size-3" />
                <span>Last interaction</span>
              </CommandItem>
            )}

            <CommandItem
              className="group !text-red-600"
              onSelect={() => handleDeleteNode()}
            >
              <Trash2Icon className="mr-2 size-3 group-hover:text-red-600" />
              <span className="group-hover:text-red-600">Delete</span>
            </CommandItem>
          </CommandGroup>
          {/* Child workflow */}
          {isChildWorkflow && (
            <Fragment>
              <CommandSeparator />
              {(childWorkflowAlias || childWorkflowId) && (
                <CommandGroup heading="Subflow">
                  <CommandItem disabled={!childIdFromAlias && !childWorkflowId}>
                    <Link
                      href={`/workspaces/${workspaceId}/workflows/${childIdFromAlias ?? childWorkflowId}`}
                      className={
                        !childIdFromAlias && !childWorkflowId
                          ? "pointer-events-none"
                          : ""
                      }
                    >
                      <div className="flex items-center">
                        <SquareArrowOutUpRightIcon className="mr-2 size-3" />
                        <span>Open subflow</span>
                      </div>
                    </Link>
                  </CommandItem>
                </CommandGroup>
              )}
            </Fragment>
          )}
        </CommandList>
      </Command>
    </NodeToolbar>
  )
}

function ChildWorkflowLink({
  workspaceId,
  childWorkflowInfo,
}: {
  workspaceId: string
  childWorkflowInfo: ChildWorkflowInfo
}) {
  const { childWorkflowId, childWorkflowAlias, childIdFromAlias } =
    childWorkflowInfo
  const { setSelectedNodeId } = useWorkflowBuilder()

  const handleClearSelection = () => {
    setSelectedNodeId(null)
  }

  if (childWorkflowId) {
    return (
      <Link
        href={`/workspaces/${workspaceId}/workflows/${childWorkflowId}`}
        onClick={handleClearSelection}
      >
        <div className="flex flex-col items-center gap-1">
          <Tooltip delayDuration={100}>
            <TooltipTrigger>
              <div className="rounded-sm border bg-muted-foreground/10 p-0.5">
                <SquareArrowOutUpRightIcon className="size-3 text-foreground/70" />
              </div>
            </TooltipTrigger>
            <TooltipContent sideOffset={20}>
              <span>
                Open the{" "}
                <TooltipCode value={childWorkflowAlias ?? childWorkflowId} />
                subflow
              </span>
            </TooltipContent>
          </Tooltip>
        </div>
      </Link>
    )
  }
  if (childWorkflowAlias) {
    if (!childIdFromAlias) {
      // Cannot get child wf ID from alias
      return (
        <Tooltip>
          <TooltipTrigger>
            <AlertTriangleIcon className="size-4 fill-red-500 stroke-white outline-red-500" />
          </TooltipTrigger>
          <TooltipContent sideOffset={20}>
            <span>
              No matching workflow found for alias{" "}
              <TooltipCode value={childWorkflowAlias} />
            </span>
          </TooltipContent>
        </Tooltip>
      )
    }
    return (
      <Link
        href={`/workspaces/${workspaceId}/workflows/${childIdFromAlias}`}
        onClick={handleClearSelection}
      >
        <div className="flex flex-col items-center gap-1">
          <Tooltip delayDuration={100}>
            <TooltipTrigger>
              <div className="rounded-sm border bg-muted-foreground/10 p-0.5">
                <SquareArrowOutUpRightIcon className="size-3 text-foreground/70" />
              </div>
            </TooltipTrigger>
            <TooltipContent sideOffset={20}>
              <span>
                Open the <TooltipCode value={childWorkflowAlias} />
                subflow
              </span>
            </TooltipContent>
          </Tooltip>
        </div>
      </Link>
    )
  }
  return (
    <Tooltip>
      <TooltipTrigger>
        <CircleHelp className="size-4 text-muted-foreground" strokeWidth={2} />
      </TooltipTrigger>
      <TooltipContent sideOffset={20}>
        <span>
          Missing <TooltipCode value="workflow_alias" />
        </span>
      </TooltipContent>
    </Tooltip>
  )
}

function TooltipCode({ value }: { value: string }) {
  return (
    <span className="m-0.5 rounded-sm border border-muted-foreground/40 bg-muted-foreground/70 p-0.5 font-mono tracking-tighter">
      {value}
    </span>
  )
}
