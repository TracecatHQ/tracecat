"use client"

import { useState } from "react"
import type { EntityMetadataRead } from "@/client"
import { IconPicker } from "@/components/form/icon-picker"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { getIconByName } from "@/lib/icon-data"

interface EntitySettingsDialogProps {
  entity: EntityMetadataRead | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onSubmit: (data: {
    display_name: string
    description?: string
    icon?: string
  }) => Promise<void>
  isPending?: boolean
}

export function EntitySettingsDialog({
  entity,
  open,
  onOpenChange,
  onSubmit,
  isPending,
}: EntitySettingsDialogProps) {
  const [displayName, setDisplayName] = useState("")
  const [description, setDescription] = useState("")
  const [selectedIcon, setSelectedIcon] = useState<string | undefined>()

  // Initialize form when entity changes
  useState(() => {
    if (entity) {
      setDisplayName(entity.display_name)
      setDescription(entity.description || "")
      setSelectedIcon(entity.icon || undefined)
    }
  })

  const handleSubmit = async () => {
    if (!displayName.trim()) return

    await onSubmit({
      display_name: displayName,
      description: description || undefined,
      icon: selectedIcon,
    })

    onOpenChange(false)
  }

  const handleCancel = () => {
    // Reset form
    if (entity) {
      setDisplayName(entity.display_name)
      setDescription(entity.description || "")
      setSelectedIcon(entity.icon || undefined)
    }
    onOpenChange(false)
  }

  if (!entity) return null

  const IconComponent = entity.icon ? getIconByName(entity.icon) : null
  const initials = entity.display_name?.[0]?.toUpperCase() || "?"

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle>Edit entity</DialogTitle>
          <DialogDescription>
            Update the display properties for this entity type.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-4">
          <div className="space-y-2">
            <Label htmlFor="name">Identifier / Slug</Label>
            <Input
              id="name"
              value={entity.name}
              disabled
              className="bg-muted"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="display_name">Name</Label>
            <Input
              id="display_name"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="Enter display name"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="description">Description</Label>
            <Textarea
              id="description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Write a description"
              className="text-xs resize-none"
              rows={3}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="icon">Icon</Label>
            <div className="flex items-center gap-3">
              {selectedIcon && (
                <Avatar className="size-8">
                  <AvatarFallback className="text-sm">
                    {(() => {
                      const Icon = getIconByName(selectedIcon)
                      return Icon ? <Icon className="size-4" /> : initials
                    })()}
                  </AvatarFallback>
                </Avatar>
              )}
              <IconPicker
                value={selectedIcon}
                onValueChange={setSelectedIcon}
                placeholder="Select an icon"
                className="flex-1"
              />
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={handleCancel} disabled={isPending}>
            Cancel
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={isPending || !displayName.trim()}
          >
            {isPending ? "Saving..." : "Save changes"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
