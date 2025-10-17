"use client"

import { format } from "date-fns"
import { CalendarClock, Clock } from "lucide-react"
import * as React from "react"

import { Button, type ButtonProps } from "@/components/ui/button"
import { Calendar } from "@/components/ui/calendar"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { cn } from "@/lib/utils"

type CalendarProps = Omit<
  React.ComponentProps<typeof Calendar>,
  "mode" | "selected" | "onSelect"
>

export interface DateTimePickerProps {
  value?: Date | null
  onChange: (value: Date | null) => void
  onBlur?: () => void
  onOpenChange?: (open: boolean) => void
  placeholder?: string
  displayFormat?: string
  formatDisplay?: (date: Date) => string
  buttonProps?: ButtonProps
  popoverContentProps?: Omit<
    React.ComponentPropsWithoutRef<typeof PopoverContent>,
    "children"
  >
  calendarProps?: CalendarProps
  timeStep?: number
  nowLabel?: string
  clearLabel?: string
  icon?: React.ReactNode
  disabled?: boolean
}

export function DateTimePicker({
  value,
  onChange,
  onBlur,
  onOpenChange,
  placeholder = "Select date and time",
  displayFormat = "PPP HH:mm",
  formatDisplay,
  buttonProps,
  popoverContentProps,
  calendarProps,
  timeStep = 60,
  nowLabel = "Now",
  clearLabel = "Clear",
  icon,
  disabled = false,
}: DateTimePickerProps) {
  const [open, setOpen] = React.useState(false)

  const dateValue = React.useMemo(() => {
    if (!value) return null
    const parsed = new Date(value)
    return Number.isNaN(parsed.getTime()) ? null : parsed
  }, [value])

  const handleSelect = React.useCallback(
    (date: Date | undefined) => {
      if (!date) {
        onChange(null)
        return
      }

      const next = new Date(date)
      if (dateValue) {
        next.setHours(dateValue.getHours(), dateValue.getMinutes(), 0, 0)
      }
      onChange(next)
    },
    [dateValue, onChange]
  )

  const handleTimeChange = React.useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      if (!dateValue) return

      const [hoursStr = "", minutesStr = ""] = event.target.value.split(":")
      const hours = Number.parseInt(hoursStr, 10)
      const minutes = Number.parseInt(minutesStr, 10)
      if (Number.isNaN(hours) || Number.isNaN(minutes)) return

      const next = new Date(dateValue)
      next.setHours(hours, minutes, 0, 0)
      onChange(next)
    },
    [dateValue, onChange]
  )

  const handleSetNow = React.useCallback(() => {
    const now = new Date()
    onChange(now)
    setOpen(false)
    onOpenChange?.(false)
    onBlur?.()
  }, [onBlur, onChange, onOpenChange])

  const handleClear = React.useCallback(() => {
    onChange(null)
    setOpen(false)
    onOpenChange?.(false)
    onBlur?.()
  }, [onBlur, onChange, onOpenChange])

  const handleOpenChange = React.useCallback(
    (nextOpen: boolean) => {
      setOpen(nextOpen)
      if (!nextOpen) {
        onBlur?.()
      }
      onOpenChange?.(nextOpen)
    },
    [onBlur, onOpenChange]
  )

  const displayValue = React.useMemo(() => {
    if (!dateValue) return placeholder
    if (formatDisplay) return formatDisplay(dateValue)
    try {
      return format(dateValue, displayFormat)
    } catch {
      return placeholder
    }
  }, [dateValue, displayFormat, formatDisplay, placeholder])

  const triggerClassName = cn(
    "justify-start text-left font-normal text-sm",
    !dateValue && "text-xs text-muted-foreground",
    buttonProps?.className
  )

  const resolvedIcon =
    icon ??
    React.createElement(CalendarClock, {
      className: "mr-2 size-4",
    })

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          {...buttonProps}
          className={triggerClassName}
          disabled={disabled || buttonProps?.disabled}
        >
          {resolvedIcon}
          {displayValue}
        </Button>
      </PopoverTrigger>
      <PopoverContent
        className="w-auto p-0"
        align="start"
        {...popoverContentProps}
      >
        <Calendar
          mode="single"
          selected={dateValue ?? undefined}
          onSelect={handleSelect}
          initialFocus
          {...calendarProps}
        />
        <div className="flex flex-col gap-2 border-t border-border p-3">
          <Input
            type="time"
            value={dateValue ? format(dateValue, "HH:mm") : ""}
            onChange={handleTimeChange}
            step={timeStep}
            disabled={!dateValue}
          />
          <div className="flex gap-2">
            <Button
              type="button"
              variant="outline"
              className="flex-1 text-xs"
              onClick={handleSetNow}
              disabled={disabled}
            >
              <Clock className="mr-2 size-4" />
              {nowLabel}
            </Button>
            <Button
              type="button"
              variant="ghost"
              className="flex-1 text-xs text-muted-foreground"
              onClick={handleClear}
              disabled={!dateValue || disabled}
            >
              {clearLabel}
            </Button>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  )
}
