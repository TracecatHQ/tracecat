"use client"

import {
  CircleAlertIcon,
  CirclePauseIcon,
  LockIcon,
  type LucideIcon,
  SearchXIcon,
} from "lucide-react"
import Link from "next/link"
import type { ReactNode } from "react"
import { formStyles } from "@/components/forms/form-styles"
import { Button } from "@/components/ui/button"
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"

/** A centered message for a requester page that has no form to show. */
export function RequesterState({
  icon: Icon,
  title,
  description,
  children,
}: {
  icon: LucideIcon
  title: string
  description: string
  /** Actions under the message. */
  children?: ReactNode
}) {
  return (
    <Empty className="gap-4 py-16">
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <Icon className="size-6" />
        </EmptyMedia>
        <EmptyTitle>{title}</EmptyTitle>
        <EmptyDescription className="text-sm">{description}</EmptyDescription>
      </EmptyHeader>
      {children && <EmptyContent>{children}</EmptyContent>}
    </Empty>
  )
}

function BrowseFormsButton() {
  return (
    <Button asChild variant="outline" className={formStyles.button}>
      <Link href="/forms">Browse forms</Link>
    </Button>
  )
}

/** The feature flag is on but the organization is not entitled to Forms. */
export function FormsUnavailableState() {
  return (
    <RequesterState
      icon={LockIcon}
      title="Forms aren't available"
      description="Contact your administrator."
    />
  )
}

/**
 * The form does not exist, or the requester cannot read it. The two cases
 * look the same on purpose.
 */
export function FormNotFoundState() {
  return (
    <RequesterState
      icon={SearchXIcon}
      title="Form not found"
      description="This form doesn't exist or you don't have access to it."
    >
      <BrowseFormsButton />
    </RequesterState>
  )
}

/** The form exists but is not taking submissions right now. */
export function FormOfflineState() {
  return (
    <RequesterState
      icon={CirclePauseIcon}
      title="This form isn't accepting requests"
      description="It may be back later. Contact the team that owns it if you need help now."
    >
      <BrowseFormsButton />
    </RequesterState>
  )
}

/** Loading failed for a reason the requester can retry. */
export function RequesterErrorState({
  title = "Something went wrong",
  onRetry,
}: {
  title?: string
  onRetry: () => void
}) {
  return (
    <RequesterState
      icon={CircleAlertIcon}
      title={title}
      description="Check your connection and try again."
    >
      <Button
        type="button"
        variant="outline"
        className={formStyles.button}
        onClick={onRetry}
      >
        Try again
      </Button>
    </RequesterState>
  )
}
