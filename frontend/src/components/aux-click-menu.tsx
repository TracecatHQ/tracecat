"use client"

import type React from "react"
import { useState } from "react"
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
import { type Client, client } from "@/lib/api"
import { cn } from "@/lib/utils"

export interface BaseAuxClickMenuOption<TData> {
  type: "item" | "sub" | "radio" | "checkbox" | "separator"
  children?: React.ReactNode
  shortcut?: React.ReactNode
  action?: (
    data: TData,
    client?: Client,
    context?: Record<string, unknown>
  ) => void
  data?: TData
}

export interface AuxClickMenuItemProps<TData>
  extends BaseAuxClickMenuOption<TData> {
  type: "item"
}

export interface AuxClickMenuSubProps<TData>
  extends BaseAuxClickMenuOption<TData> {
  type: "sub"
  items?: AuxClickMenuOptionProps<TData>[]
}
interface AuxClickMenuRadioProps<TData> extends BaseAuxClickMenuOption<TData> {
  type: "radio"
  defaultValue?: string
  items?: { title: string; value: string }[]
}
interface AuxClickMenuCheckboxProps<TData>
  extends BaseAuxClickMenuOption<TData> {
  type: "checkbox"
}
interface AuxClickMenuSeparatorProps<TData>
  extends BaseAuxClickMenuOption<TData> {
  type: "separator"
}

export type AuxClickMenuOptionProps<TData> =
  | AuxClickMenuItemProps<TData>
  | AuxClickMenuSubProps<TData>
  | AuxClickMenuRadioProps<TData>
  | AuxClickMenuCheckboxProps<TData>
  | AuxClickMenuSeparatorProps<TData>

export interface AuxClickMenuProps<TData>
  extends React.PropsWithChildren<React.HTMLAttributes<HTMLButtonElement>> {
  options?: AuxClickMenuOptionProps<TData>[]
  data: TData // Currently working with a Column<Case> type
}
export default function AuxClickMenu<TData>({
  className,
  children,
  options,
  data,
}: AuxClickMenuProps<TData>) {
  return options ? (
    <ContextMenu>
      <ContextMenuTrigger asChild>{children}</ContextMenuTrigger>
      <ContextMenuContent className={cn("w-64", className)}>
        {options.map((optionProps, idx) => (
          <DynamicAuxClickMenuOption key={idx} {...optionProps} data={data} />
        ))}
      </ContextMenuContent>
    </ContextMenu>
  ) : (
    children
  )
}
function DynamicAuxClickMenuOption<TData>(
  props: AuxClickMenuOptionProps<TData>
) {
  switch (props.type) {
    case "item":
      return <AuxClickMenuItem {...props} />
    case "sub":
      return <AuxClickMenuSub {...props} />
    case "radio":
      return <AuxClickMenuRadio {...props} />
    case "checkbox":
      return <AuxClickMenuCheckbox {...props} />
    case "separator":
      return <AuxClickMenuSeparator {...props} />
    default:
      return null
  }
}

export function AuxClickMenuItem<TData>({
  children,
  shortcut,
  action,
  data,
}: AuxClickMenuItemProps<TData>) {
  // If we fail to get the current session, this should kick us out
  return (
    <ContextMenuItem
      className={cn("hover:cursor-pointer")}
      onClick={() => {
        if (data) {
          action?.(data, client)
        }
      }}
      inset
    >
      {children}
      {shortcut && <ContextMenuShortcut>{shortcut}</ContextMenuShortcut>}
    </ContextMenuItem>
  )
}

export function AuxClickMenuSub<TData>({
  children,
  items,
}: AuxClickMenuSubProps<TData>) {
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

export function AuxClickMenuRadio<TData>({
  children,
  defaultValue = "",
  items,
  ...props
}: AuxClickMenuRadioProps<TData>) {
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

export function AuxClickMenuCheckbox<TData>({
  children,
  shortcut,
  ...props
}: AuxClickMenuCheckboxProps<TData>) {
  return (
    <ContextMenuCheckboxItem {...props} className="hover:cursor-pointer">
      {children}
      {shortcut && <ContextMenuShortcut>{shortcut}</ContextMenuShortcut>}
    </ContextMenuCheckboxItem>
  )
}

export const AuxClickMenuSeparator = ContextMenuSeparator
