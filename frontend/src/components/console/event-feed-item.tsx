import { UpdateIcon } from "@radix-ui/react-icons"
import SyntaxHighlighter from "react-syntax-highlighter"
import { atomOneDark } from "react-syntax-highlighter/dist/esm/styles/hljs"

import { ActionRun, RunStatus, WorkflowRun } from "@/types/schemas"
import { cn, parseActionRunId, undoSlugify } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Card } from "@/components/ui/card"
import {
  consoleSchemaMap,
  GenericConsoleEvent,
} from "@/components/console/console"
import DecoratedHeader from "@/components/decorated-header"

export function EventFeedItem({ type, ...props }: GenericConsoleEvent) {
  const validatedProps = consoleSchemaMap[type].parse(props)
  switch (type) {
    case "workflow_run":
      return <WorkflowRunEvent {...(validatedProps as WorkflowRun)} />
    case "action_run":
      return <ActionRunEvent {...(validatedProps as ActionRun)} />
  }
}

function WorkflowRunEvent({ id, status, created_at, updated_at }: WorkflowRun) {
  const eventType = (
    <span>
      Workflow run <b>{id}</b>
    </span>
  )
  const { badgeLabel, badgeStyle, text, textStyle } = getRunStatusStyle(
    status,
    eventType
  )
  return (
    <Card className="mr-2 flex w-full items-center justify-between p-4 shadow-sm">
      <DecoratedHeader
        size="sm"
        node={
          <span className="flex items-center">
            <Badge variant="outline" className={cn("mr-2", badgeStyle)}>
              {badgeLabel}
            </Badge>
            <span>
              {created_at.toLocaleDateString()}{" "}
              {created_at.toLocaleTimeString()}
            </span>
            <span
              className={cn("ml-4 flex items-center font-normal", textStyle)}
            >
              {text}
            </span>
          </span>
        }
        iconProps={{
          className: cn("stroke-2", badgeStyle),
        }}
        className="font-medium"
      />
      <span className="flex items-center justify-center text-xs text-muted-foreground">
        <UpdateIcon className="mr-1 h-3 w-3" />
        {updated_at.toLocaleTimeString()}
      </span>
    </Card>
  )
}

function ActionRunEvent({
  status,
  created_at,
  updated_at,
  id,
  result,
  error_msg,
}: ActionRun) {
  const title = undoSlugify(parseActionRunId(id))
  const eventType = (
    <span>
      Action <b>{title}</b>
    </span>
  )
  const { badgeLabel, badgeStyle, text, textStyle } = getRunStatusStyle(
    status,
    eventType
  )
  return (
    <Card className="mr-2 flex w-full flex-col space-y-4 p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <DecoratedHeader
          size="sm"
          node={
            <span className="flex items-center">
              <Badge variant="outline" className={cn("mr-2", badgeStyle)}>
                {badgeLabel}
              </Badge>
              <span>
                {created_at.toLocaleDateString()}{" "}
                {created_at.toLocaleTimeString()}
              </span>
              <span
                className={cn("ml-4 flex items-center font-normal", textStyle)}
              >
                {text}
              </span>
            </span>
          }
          iconProps={{
            className: cn("stroke-2", badgeStyle),
          }}
          className="font-medium"
        />
        <span className="flex items-center justify-center text-xs text-muted-foreground">
          <UpdateIcon className="mr-1 h-3 w-3" />
          {updated_at.toLocaleTimeString()}
        </span>
      </div>
      <EventFeedItemResultOrError result={result} error_msg={error_msg} />
    </Card>
  )
}

function getRunStatusStyle(
  status: RunStatus,
  eventType: string | React.ReactNode
): {
  badgeLabel: string
  badgeStyle: string
  text: string | React.ReactNode
  textStyle: string
} {
  switch (status) {
    case "success":
      return {
        badgeLabel: "Success",
        badgeStyle: "bg-green-500/50 border-green-500 text-green-700",
        text: <span>{eventType} completed successfully 🎉</span>,
        textStyle: "text-muted-foreground",
      }
    case "failure":
      return {
        badgeLabel: "Failure",
        badgeStyle: "bg-red-500/50 border-red-500 text-red-700",
        text: <span>{eventType} failed ⛔</span>,
        textStyle: "text-muted-foreground",
      }
    case "running":
      return {
        badgeLabel: "Running",
        badgeStyle: "bg-blue-500/50 border-blue-500 text-blue-700",
        text: <span>{eventType} started</span>,
        textStyle: "text-muted-foreground",
      }
    case "pending":
      return {
        badgeLabel: "Pending",
        badgeStyle: "bg-amber-500/50 border-amber-500 text-amber-700",
        text: <span>{eventType} is waiting to start</span>,
        textStyle: "text-muted-foreground italic",
      }
    case "canceled":
      return {
        badgeLabel: "Canceled",
        badgeStyle: "bg-orange-500/50 border-orange-500 text-orange-700",
        text: <span>{eventType} was canceled</span>,
        textStyle: "text-muted-foreground",
      }
    default:
      throw new Error("Invalid status")
  }
}

function EventFeedItemResultOrError({
  result,
  error_msg,
}: {
  result: Record<string, unknown> | null
  error_msg: string | null
}) {
  if (!result && !error_msg) {
    return null
  }
  return (
    <div className="rounded-md">
      {result ? (
        <SyntaxHighlighter
          language={result ? "json" : undefined}
          style={atomOneDark}
          wrapLines
          wrapLongLines={error_msg ? true : false}
          customStyle={{
            width: "100%",
            maxWidth: "100%",
            overflowX: "auto",
          }}
          codeTagProps={{
            className:
              "text-xs text-background rounded-md max-w-full overflow-auto",
          }}
          {...{
            className:
              "rounded-md p-4 overflow-auto max-w-full w-full no-scrollbar",
          }}
        >
          {JSON.stringify(result.output, null, 2)}
        </SyntaxHighlighter>
      ) : (
        <pre className="h-full w-full overflow-auto text-wrap rounded-md bg-[#292c33] p-2">
          <code className="max-w-full overflow-auto rounded-md text-xs text-red-400/80">
            {error_msg}
          </code>
        </pre>
      )}
    </div>
  )
}
