import { $TriggerType, TriggerType } from "@/client"
import { FilterIcon } from "lucide-react"

import { useLocalStorage } from "@/lib/hooks"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { getTriggerTypeIcon } from "@/components/builder/events/events-workflow"

const AVAILABLE_TRIGGER_TYPES: readonly TriggerType[] = $TriggerType.enum
export function EventsSidebarToolbar() {
  const [selectedTriggerTypes, setSelectedTriggerTypes] = useLocalStorage<
    TriggerType[]
  >("selected-trigger-types", [...AVAILABLE_TRIGGER_TYPES])

  const handleTriggerTypeToggle = (triggerType: TriggerType) => {
    if (selectedTriggerTypes.includes(triggerType)) {
      // Don't allow removing the last trigger type
      if (selectedTriggerTypes.length <= 1) {
        return
      }
      const newTypes = selectedTriggerTypes.filter(
        (type) => type !== triggerType
      )
      setSelectedTriggerTypes(newTypes)
    } else {
      // Add the trigger type
      setSelectedTriggerTypes([...selectedTriggerTypes, triggerType])
    }
  }

  return (
    <div
      id="events-sidebar-toolbar-wrapper"
      className={`absolute bottom-4 right-4 z-20`}
    >
      <div
        id="events-sidebar-toolbar"
        className="flex items-center justify-center rounded-lg border border-input bg-background shadow-md [&>*]:h-8 [&>:first-child]:rounded-l-lg [&>:last-child]:rounded-r-lg"
      >
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              className="flex items-center gap-1 rounded-none px-3 py-2"
            >
              <FilterIcon className="size-4 text-foreground/70" />
              <span className="whitespace-nowrap text-xs text-foreground/70">
                Filter
              </span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuLabel className="text-xs text-muted-foreground">
              Trigger Types
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            {AVAILABLE_TRIGGER_TYPES.map((triggerType) => (
              <DropdownMenuCheckboxItem
                key={triggerType}
                checked={selectedTriggerTypes.includes(triggerType)}
                onCheckedChange={() => handleTriggerTypeToggle(triggerType)}
              >
                <div className="flex items-center gap-2">
                  {getTriggerTypeIcon(triggerType)}
                  <span>
                    {triggerType.charAt(0).toUpperCase() + triggerType.slice(1)}
                  </span>
                </div>
              </DropdownMenuCheckboxItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
        {/* <Button
          variant="ghost"
          className="flex items-center gap-1 rounded-none px-3 py-2"
          onClick={toggleToolbarPosition}
        >
          <SettingsIcon className="size-4 text-foreground/70" />
          <span className="whitespace-nowrap text-xs text-foreground/70">
            Settings
          </span>
        </Button> */}
      </div>
    </div>
  )
}
