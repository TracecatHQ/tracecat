"use client"

import { ComponentPropsWithoutRef } from "react"
import { useFormStatus } from "react-dom"

import { Button } from "@/components/ui/button"

type Props = ComponentPropsWithoutRef<typeof Button> & {
  pendingText?: string
}

export function SubmitButton({ children, pendingText, ...props }: Props) {
  const { pending, action } = useFormStatus()

  const isPending = pending && action === props.formAction

  return (
    <Button {...props} type="submit" aria-disabled={pending}>
      {isPending ? pendingText : children}
    </Button>
  )
}
