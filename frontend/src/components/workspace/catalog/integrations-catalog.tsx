import { DragEvent } from "react"
import { ScrollArea } from "@radix-ui/react-scroll-area"
import { Node } from "reactflow"

import { Integration, IntegrationPlatform } from "@/types/schemas"
import { useIntegrations } from "@/lib/integrations"
import { groupBy, undoSlugify } from "@/lib/utils"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import DecoratedHeader from "@/components/decorated-header"
import { Integrations } from "@/components/icons"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"

export type IntegrationNodeType = Node<Integration>

export const INTEGRATION_NODE_TAG = "integrations"

export default function IntegrationsCatalog() {
  const { integrations, isLoading, error } = useIntegrations()
  const onDragStart = (
    event: DragEvent<HTMLDivElement>,
    integration: Integration
  ) => {
    console.log("Dragging node", INTEGRATION_NODE_TAG)
    event.dataTransfer.setData("application/reactflow", INTEGRATION_NODE_TAG)
    const integrationData = {
      type: `integrations.${integration.platform}.${integration.name}`,
      title: undoSlugify(integration.name),
      status: "offline",
      isConfigured: false,
      numberOfEvents: 0,
    }
    event.dataTransfer.setData(
      "application/json",
      JSON.stringify(integrationData)
    )
    event.dataTransfer.effectAllowed = "move"
  }

  return (
    <div className="flex h-full select-none flex-col space-y-4 p-4 text-sm">
      {error ? (
        <AlertNotification
          level="error"
          title="Error"
          message={error.message}
        />
      ) : isLoading ? (
        <CenteredSpinner />
      ) : (
        <ScrollArea className="no-scrollbar h-full overflow-auto rounded-md">
          <Accordion type="single" collapsible className="w-full">
            {Object.entries(
              groupBy<Integration, "platform">(integrations, "platform")
            ).map(([platform, integrations], index) => {
              const Icon = Integrations[platform as IntegrationPlatform]
              return (
                <AccordionItem key={index} value={platform}>
                  <AccordionTrigger>
                    <div className="mr-2 flex max-h-12 w-full items-center justify-start gap-4">
                      <Icon className="size-5" />
                      <DecoratedHeader
                        size="xs"
                        node={undoSlugify(platform)}
                        className="font-medium"
                      />
                    </div>
                  </AccordionTrigger>
                  <AccordionContent>
                    <div className="mt-2 items-center">
                      <CatalogSection
                        integrations={integrations}
                        dragHandler={onDragStart}
                      />
                    </div>
                  </AccordionContent>
                </AccordionItem>
              )
            })}
          </Accordion>
        </ScrollArea>
      )}
    </div>
  )
}

interface CatalogSectionProps extends React.HTMLAttributes<HTMLDivElement> {
  integrations: Integration[]
  dragHandler: (
    event: DragEvent<HTMLDivElement>,
    integration: Integration
  ) => void
}
function CatalogSection({ integrations, dragHandler }: CatalogSectionProps) {
  return (
    <div className="space-y-2">
      {integrations.map((integration, idx) => {
        return (
          <CatalogItem
            key={idx}
            onDragStart={(event) => dragHandler(event, integration)}
            draggable
            integration={integration}
            Icon={Integrations[integration.platform]}
          />
        )
      })}
    </div>
  )
}

export interface CatalogItemProps extends React.HTMLAttributes<HTMLDivElement> {
  integration: Integration
  Icon: React.FC<React.SVGProps<SVGSVGElement>>
}

export function CatalogItem({ integration, Icon, ...props }: CatalogItemProps) {
  return (
    <div
      className="flex items-center rounded-lg p-2 text-start transition-all hover:cursor-grab hover:bg-accent"
      {...props}
    >
      <Icon className="mr-2 size-5 shrink-0" />
      <div className="flex min-h-8 flex-col items-center">
        <span className="grow text-xs">{undoSlugify(integration.name)}</span>
      </div>
    </div>
  )
}
