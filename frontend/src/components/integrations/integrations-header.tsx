"use client"

import {
  KeyRound,
  Link2,
  Lock,
  LockKeyhole,
  Search,
  Sparkles,
  Unlink2,
  WrenchIcon,
} from "lucide-react"
 import { Input } from "@/components/ui/input"
 import {
   Select,
   SelectContent,
   SelectItem,
   SelectTrigger,
   SelectValue,
 } from "@/components/ui/select"
 import { cn } from "@/lib/utils"

export type IntegrationTypeFilter =
  | "credential"
  | "oauth"
  | "custom_oauth"
  | "mcp"
  | "custom_mcp"
 export type ConnectionFilter = "all" | "connected" | "not_connected"

 interface IntegrationsHeaderProps {
   searchQuery: string
   onSearchChange: (query: string) => void
   typeFilters: IntegrationTypeFilter[]
   onTypeFilterToggle: (filter: IntegrationTypeFilter) => void
   connectionFilter: ConnectionFilter
   onConnectionFilterChange: (filter: ConnectionFilter) => void
 }

const TYPE_FILTER_OPTIONS: Array<{
   value: IntegrationTypeFilter
   label: string
  icon: typeof KeyRound
 }> = [
  { value: "credential", label: "Credentials", icon: KeyRound },
  { value: "oauth", label: "OAuth", icon: Lock },
  { value: "custom_oauth", label: "Custom OAuth", icon: LockKeyhole },
  { value: "mcp", label: "MCP", icon: Sparkles },
  { value: "custom_mcp", label: "Custom MCP", icon: WrenchIcon },
 ]

 const filterButtonClassName =
   "flex h-6 items-center gap-1.5 rounded-md border border-input bg-transparent px-2 text-xs font-medium transition-colors hover:bg-muted/50"

 export function IntegrationsHeader({
   searchQuery,
   onSearchChange,
   typeFilters,
   onTypeFilterToggle,
   connectionFilter,
   onConnectionFilterChange,
 }: IntegrationsHeaderProps) {
  return (
    <div className="w-full shrink-0 border-b">
      {/* Row 1: Search */}
      <header className="flex h-10 w-full items-center border-b pl-3 pr-4">
         <div className="flex min-w-0 items-center gap-3">
           <div className="flex h-7 w-7 shrink-0 items-center justify-center">
             <Search className="size-4 text-muted-foreground" />
           </div>
           <Input
             type="text"
             placeholder="Search integrations..."
             value={searchQuery}
             onChange={(e) => onSearchChange(e.target.value)}
             className={cn(
               "h-7 w-48 border-none bg-transparent p-0",
               "text-sm",
               "shadow-none outline-none",
               "placeholder:text-muted-foreground",
               "focus-visible:ring-0 focus-visible:ring-offset-0"
             )}
           />
         </div>
       </header>

      {/* Row 2: Filters */}
      <div className="flex w-full flex-wrap items-center gap-2 py-2 pl-3 pr-4">
         {TYPE_FILTER_OPTIONS.map((option) => {
           const isActive = typeFilters.includes(option.value)
          const Icon = option.icon
           return (
             <button
               key={option.value}
               type="button"
               className={cn(
                 filterButtonClassName,
                 isActive && "border-primary/50 bg-primary/5"
               )}
               aria-pressed={isActive}
               onClick={() => onTypeFilterToggle(option.value)}
             >
              <Icon className="size-3.5 text-muted-foreground" />
               {option.label}
             </button>
           )
         })}
         <Select
           value={connectionFilter}
           onValueChange={(value) =>
             onConnectionFilterChange(value as ConnectionFilter)
           }
         >
           <SelectTrigger
             className={cn(
               "h-6 w-[170px] rounded-md border border-input bg-transparent px-2 text-xs font-medium",
               "hover:bg-muted/50",
               connectionFilter !== "all" && "border-primary/50 bg-primary/5"
             )}
           >
             <SelectValue placeholder="Connection" />
           </SelectTrigger>
           <SelectContent>
             <SelectItem value="all">All connections</SelectItem>
            <SelectItem value="connected">
              <span className="flex items-center gap-2">
                <Link2 className="size-3.5 text-muted-foreground" />
                Connected
              </span>
            </SelectItem>
            <SelectItem value="not_connected">
              <span className="flex items-center gap-2">
                <Unlink2 className="size-3.5 text-muted-foreground" />
                Not connected
              </span>
            </SelectItem>
           </SelectContent>
         </Select>
       </div>
     </div>
   )
 }
