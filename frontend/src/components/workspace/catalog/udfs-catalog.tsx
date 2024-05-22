import { DragEvent } from "react"
import { ScrollArea } from "@radix-ui/react-scroll-area"
import { Node } from "reactflow"

import { UDF, useUDFs } from "@/lib/udf"
import { groupBy, undoSlugify } from "@/lib/utils"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import DecoratedHeader from "@/components/decorated-header"
import { getIcon } from "@/components/icons"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"

export type UDFNodeType = Node<UDF>

export const UDF_NODE_TAG = "udf"

export default function UDFCatalog() {
  const { udfs, isLoading, error } = useUDFs()
  const onDragStart = (event: DragEvent<HTMLDivElement>, udf: UDF) => {
    console.log("Dragging node", UDF_NODE_TAG)
    event.dataTransfer.setData("application/reactflow", UDF_NODE_TAG)
    event.dataTransfer.setData(
      "application/json",
      JSON.stringify({
        type: udf.key,
        title: udf.key,
        namespace: udf.namespace,
        status: "offline",
        isConfigured: false,
        numberOfEvents: 0,
      })
    )
    event.dataTransfer.effectAllowed = "move"
  }

  if (!udfs || isLoading) {
    return <CenteredSpinner />
  }

  // We're just going to do very simple grouping of nodes by namespace
  // Leave the complicated tree-grouping for later

  return (
    <div className="flex h-full select-none flex-col space-y-4 p-4 text-sm">
      {error ? (
        <AlertNotification
          level="error"
          title="Error"
          message={error.message.slice(0, 100)}
        />
      ) : isLoading ? (
        <CenteredSpinner />
      ) : (
        <ScrollArea className="no-scrollbar h-full overflow-auto rounded-md">
          <Accordion type="multiple" className="w-full">
            {Object.entries(groupBy(udfs, "namespace")).map(
              ([namespace, udfs], index) => {
                return (
                  <AccordionItem
                    key={index}
                    value={namespace}
                    className="border-b-0"
                  >
                    <AccordionTrigger>
                      <div className="mr-2 flex max-h-12 w-full items-center justify-start gap-4">
                        {getIcon(namespace, { className: "size-5" })}
                        <DecoratedHeader
                          size="xs"
                          node={undoSlugify(namespace)}
                          className="font-medium"
                        />
                      </div>
                    </AccordionTrigger>
                    <AccordionContent>
                      <div className="mt-2 items-center">
                        <CatalogSection udfs={udfs} dragHandler={onDragStart} />
                      </div>
                    </AccordionContent>
                  </AccordionItem>
                )
              }
            )}
          </Accordion>
        </ScrollArea>
      )}
    </div>
  )
}

interface CatalogSectionProps extends React.HTMLAttributes<HTMLDivElement> {
  udfs: UDF[]
  dragHandler: (event: DragEvent<HTMLDivElement>, udf: UDF) => void
}
function CatalogSection({ udfs, dragHandler }: CatalogSectionProps) {
  return (
    <div className="ml-3 space-y-2 border-l pl-1">
      {udfs.map((udf, idx) => {
        return (
          <CatalogItem
            key={idx}
            onDragStart={(event) => dragHandler(event, udf)}
            draggable
            udf={udf}
          />
        )
      })}
    </div>
  )
}

interface CatalogItemProps extends React.HTMLAttributes<HTMLDivElement> {
  udf: UDF
}

function CatalogItem({ udf, ...props }: CatalogItemProps) {
  return (
    <div
      className="flex items-center rounded-lg p-2 text-start transition-all hover:cursor-grab hover:bg-accent"
      {...props}
    >
      <div className="size-6 rounded-full">
        {getIcon(udf.key, { className: "size-5" })}
      </div>
      <div className="ml-2 flex min-h-8 flex-col items-center justify-center">
        <span className="text-xs">{undoSlugify(udf.key)}</span>
      </div>
    </div>
  )
}
