import { LifeBuoy, SquareTerminal, SquareUser } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { Console } from "@/components/console/console"

export default function Dashboard() {
  return (
    <TooltipProvider>
      <div className="flex-1 overflow-auto">
        <div className="grid h-full max-h-full w-full pl-[53px]">
          <SideNav />
          <Console />
        </div>
      </div>
    </TooltipProvider>
  )
}

function SideNav() {
  return (
    <aside className="inset-y fixed  left-0 z-20 flex h-full flex-col border-r">
      <nav className="grid gap-1 p-2">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="rounded-lg"
              aria-label="Feed"
            >
              <SquareTerminal className="size-5" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="right" sideOffset={5}>
            Feed
          </TooltipContent>
        </Tooltip>
      </nav>
    </aside>
  )
}
