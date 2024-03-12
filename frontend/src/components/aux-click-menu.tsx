import React, { useState } from "react"

import { cn } from "@/lib/utils"
import {
  ContextMenu,
  ContextMenuCheckboxItem,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuLabel,
  ContextMenuRadioGroup,
  ContextMenuRadioItem,
  ContextMenuSeparator,
  ContextMenuShortcut,
  ContextMenuSub,
  ContextMenuSubContent,
  ContextMenuSubTrigger,
  ContextMenuTrigger,
} from "@/components/ui/context-menu"

export interface BaseAuxClickMenuOption {
  type: "item" | "sub" | "radio" | "checkbox" | "separator"
  icon?: React.ReactNode
  children?: React.ReactNode
  shortcut?: React.ReactNode
  onClick?: () => void
}

export interface AuxClickMenuItemProps extends BaseAuxClickMenuOption {
  type: "item"
}

export interface AuxClickMenuSubProps extends BaseAuxClickMenuOption {
  type: "sub"
  items?: AuxClickMenuOptionProps[]
}
interface AuxClickMenuRadioProps extends BaseAuxClickMenuOption {
  type: "radio"
  defaultValue?: string
  items?: { title: string; value: string }[]
}
interface AuxClickMenuCheckboxProps extends BaseAuxClickMenuOption {
  type: "checkbox"
}
interface AuxClickMenuSeparatorProps extends BaseAuxClickMenuOption {
  type: "separator"
}

export type AuxClickMenuOptionProps =
  | AuxClickMenuItemProps
  | AuxClickMenuSubProps
  | AuxClickMenuRadioProps
  | AuxClickMenuCheckboxProps
  | AuxClickMenuSeparatorProps

export interface AuxClickMenuProps
  extends React.PropsWithChildren<React.HTMLAttributes<HTMLButtonElement>> {
  options?: AuxClickMenuOptionProps[]
}
export default function AuxClickMenu({
  className,
  children,
  options,
}: AuxClickMenuProps) {
  return options ? (
    <ContextMenu>
      <ContextMenuTrigger asChild>{children}</ContextMenuTrigger>
      <ContextMenuContent className={cn("w-64", className)}>
        {options.map((option, idx) => (
          <DynamicAuxClickMenuOption key={idx} {...option} />
        ))}
      </ContextMenuContent>
    </ContextMenu>
  ) : (
    children
  )
}
function DynamicAuxClickMenuOption(option: AuxClickMenuOptionProps) {
  switch (option.type) {
    case "item":
      return <AuxClickMenuItem {...option} />
    case "sub":
      return <AuxClickMenuSub {...option} />
    case "radio":
      return <AuxClickMenuRadio {...option} />
    case "checkbox":
      return <AuxClickMenuCheckbox {...option} />
    case "separator":
      return <AuxClickMenuSeparator {...option} />
    default:
      return null
  }
}

export function AuxClickMenuItem({
  children,
  shortcut,
  icon,
  onClick,
}: AuxClickMenuItemProps) {
  return (
    <ContextMenuItem
      className={cn("hover:cursor-pointer")}
      onClick={onClick}
      inset
    >
      {children}
      {shortcut && <ContextMenuShortcut>{shortcut}</ContextMenuShortcut>}
    </ContextMenuItem>
  )
}

export function AuxClickMenuSub({ children, items }: AuxClickMenuSubProps) {
  return (
    <ContextMenuSub>
      <ContextMenuSubTrigger inset className="hover:cursor-pointer">
        {children}
      </ContextMenuSubTrigger>
      <ContextMenuSubContent className={cn("w-48")}>
        {items?.map((item, idx) => (
          <DynamicAuxClickMenuOption key={idx} {...item} />
        ))}
      </ContextMenuSubContent>
    </ContextMenuSub>
  )
}

export function AuxClickMenuRadio({
  children,
  defaultValue = "",
  items,
  ...props
}: AuxClickMenuRadioProps) {
  const [currValue, setCurrValue] = useState<string>(defaultValue)
  return (
    <ContextMenuRadioGroup value={currValue} {...props}>
      <ContextMenuLabel inset>{children}</ContextMenuLabel>
      <ContextMenuSeparator />
      {items?.map(({ title, value }, idx) => (
        <ContextMenuRadioItem
          key={idx}
          value={value}
          onClick={() => setCurrValue(value)}
          className="hover:cursor-pointer"
        >
          {title}
        </ContextMenuRadioItem>
      ))}
    </ContextMenuRadioGroup>
  )
}

export function AuxClickMenuCheckbox({
  children,
  shortcut,
  ...props
}: AuxClickMenuCheckboxProps) {
  return (
    <ContextMenuCheckboxItem {...props} className="hover:cursor-pointer">
      {children}
      {shortcut && <ContextMenuShortcut>{shortcut}</ContextMenuShortcut>}
    </ContextMenuCheckboxItem>
  )
}

export const AuxClickMenuSeparator = ContextMenuSeparator
