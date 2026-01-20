import type { InboxItemRead } from "@/client"
import { ScrollArea } from "@/components/ui/scroll-area"

import { InboxListItem } from "./inbox-list-item"

interface InboxListHeaderProps {
  itemCount: number
}

function InboxListHeader({ itemCount }: InboxListHeaderProps) {
  return (
    <div className="flex h-12 shrink-0 items-center border-b px-3">
      <span className="text-sm font-medium">Inbox</span>
      {itemCount > 0 && (
        <span className="ml-2 text-xs text-muted-foreground">{itemCount}</span>
      )}
    </div>
  )
}

interface InboxListProps {
  items: InboxItemRead[]
  selectedId: string | null
  onSelect: (id: string) => void
}

export function InboxList({ items, selectedId, onSelect }: InboxListProps) {
  return (
    <div className="flex h-full flex-col">
      <InboxListHeader itemCount={items.length} />
      <ScrollArea className="flex-1">
        <div className="divide-y">
          {items.map((item) => (
            <InboxListItem
              key={item.id}
              item={item}
              isSelected={selectedId === item.id}
              onClick={() => onSelect(item.id)}
            />
          ))}
        </div>
        {items.length === 0 && (
          <div className="flex h-32 items-center justify-center">
            <p className="text-sm text-muted-foreground">No items</p>
          </div>
        )}
      </ScrollArea>
    </div>
  )
}
