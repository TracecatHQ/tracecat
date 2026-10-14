"use client"

import { format } from "date-fns"
import {
  ChevronRightIcon,
  CircleAlertIcon,
  CirclePauseIcon,
  ClipboardListIcon,
  InboxIcon,
  LockIcon,
  LogOutIcon,
  type LucideIcon,
  SearchXIcon,
} from "lucide-react"
import Link from "next/link"
import { notFound, useRouter, useSearchParams } from "next/navigation"
import { type ReactNode, useEffect, useState } from "react"
import {
  type FormIntakeRead,
  usersGetMyScopes,
  workspacesListWorkspaces,
} from "@/client"
import {
  FormRenderer,
  MY_SUBMISSIONS_HREF,
} from "@/components/forms/form-renderer"
import { formStyles, requesterStyles } from "@/components/forms/form-styles"
import { Icons } from "@/components/icons"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import UserAvatar from "@/components/user-avatar"
import { useAuth, useAuthActions } from "@/hooks/use-auth"
import {
  type CursorList,
  useFormLocation,
  useRenderedForm,
  useRequesterForms,
  useRequesterSubmissions,
  useSubmitForm,
} from "@/hooks/use-forms"
import { useFormsAccess } from "@/hooks/use-forms-access"
import {
  getFormErrorCode,
  getFormLoadFailure,
  isFormOfflineError,
} from "@/lib/forms/errors"
import { uploadFormFile } from "@/lib/forms/files"
import { formMarkdownToPlainText } from "@/lib/forms/markdown"
import type { FormValues } from "@/lib/forms/values"
import { useUserScopes } from "@/lib/hooks"
import { useQueries, useQuery } from "@/lib/query"
import { hasGrantedScope } from "@/lib/scopes"
import { cn } from "@/lib/utils"

const STALE_TIME_MS = 5 * 60 * 1000

/** A centered message for a requester page that has no form to show. */
function RequesterState({
  icon: Icon,
  title,
  description,
  children,
}: {
  icon: LucideIcon
  title: string
  description: string
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

/** The form is offline, or missing, which is how an unreadable one looks. */
function FormUnavailableState({ offline }: { offline: boolean }) {
  return (
    <RequesterState
      icon={offline ? CirclePauseIcon : SearchXIcon}
      title={offline ? "This form isn't accepting requests" : "Form not found"}
      description={
        offline
          ? "It may be back later. Contact the team that owns it if you need help now."
          : "This form doesn't exist or you don't have access to it."
      }
    >
      <Button asChild variant="outline" className={formStyles.button}>
        <Link href="/forms">Browse forms</Link>
      </Button>
    </RequesterState>
  )
}

/** Loading failed for a reason the requester can retry. */
function RetryState({
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
      <Button variant="outline" className={formStyles.button} onClick={onRetry}>
        Try again
      </Button>
    </RequesterState>
  )
}

/**
 * "Open workspace" menu items: the workspaces where the user holds
 * `workspace:read`, their way back from the intake redirect. Asks for scopes
 * per workspace, so it is mounted only while the menu is open; the query keys
 * match `useWorkspaceManager` and `useUserScopes`.
 */
function WorkspaceMenuItems() {
  const { data: workspaces = [] } = useQuery({
    queryKey: ["workspaces"],
    queryFn: () => workspacesListWorkspaces(),
    staleTime: STALE_TIME_MS,
    meta: { suppressErrorToast: true },
  })
  const scopes = useQueries({
    queries: workspaces.map((workspace) => ({
      queryKey: ["user-scopes", workspace.id],
      queryFn: () => usersGetMyScopes({ workspaceId: workspace.id }),
      staleTime: STALE_TIME_MS,
      meta: { suppressErrorToast: true },
    })),
  })
  const readable = workspaces.filter((_workspace, index) =>
    hasGrantedScope("workspace:read", scopes[index]?.data?.scopes ?? [])
  )
  if (readable.length === 0) {
    return null
  }
  return (
    <>
      <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">
        Open workspace
      </DropdownMenuLabel>
      {readable.map((workspace) => (
        <DropdownMenuItem key={workspace.id} className="text-sm" asChild>
          <Link href={`/workspaces/${workspace.id}`}>
            <span className="truncate">{workspace.name}</span>
          </Link>
        </DropdownMenuItem>
      ))}
      <DropdownMenuSeparator />
    </>
  )
}

/** A requester page: logo, title linking to the catalog, account menu. */
function RequesterPage({
  title,
  children,
}: {
  title: string
  children: ReactNode
}) {
  const { user } = useAuth()
  const { logout } = useAuthActions()
  return (
    <>
      <header className={requesterStyles.topBar}>
        <Link
          href="/forms"
          aria-label={`${title}, all forms`}
          className="flex min-w-0 items-center gap-2.5"
        >
          <Icons.logo className="size-6 shrink-0" />
          <span className={requesterStyles.topBarTitle}>{title}</span>
        </Link>
        <span className="flex-1" />
        {user && (
          <DropdownMenu>
            <DropdownMenuTrigger
              aria-label={`Account menu for ${user.getDisplayName()}`}
              className="rounded-full focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            >
              <UserAvatar
                email={user.email}
                firstName={user.firstName}
                className="size-7"
                fallbackClassName="text-xs"
              />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56">
              <DropdownMenuLabel className="flex flex-col gap-0.5 font-normal">
                <span className="truncate text-sm font-medium">
                  {user.getDisplayName()}
                </span>
                <span className="truncate text-xs text-muted-foreground">
                  {user.email}
                </span>
              </DropdownMenuLabel>
              <DropdownMenuSeparator />
              <WorkspaceMenuItems />
              <DropdownMenuItem className="text-sm" onSelect={() => logout()}>
                <LogOutIcon className="mr-2 size-4" />
                Sign out
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </header>
      <main className={requesterStyles.main}>{children}</main>
    </>
  )
}

function RequesterGate({ children }: { children: ReactNode }) {
  const access = useFormsAccess()
  if (access === "hidden") {
    notFound()
  }
  if (access === "loading") {
    return <CenteredSpinner />
  }
  if (access === "locked") {
    return (
      <RequesterPage title="Forms">
        <RequesterState
          icon={LockIcon}
          title="Forms aren't available"
          description="Contact your administrator."
        />
      </RequesterPage>
    )
  }
  return <>{children}</>
}

/**
 * Frame and gate for the requester pages under `/forms`, which sit outside
 * the workspace shell. A signed-out visitor is sent to sign-in with a return
 * URL; only a signed-in one mounts the gate that reads the Forms flag and
 * entitlement, so a signed-out visit fires no authenticated request.
 */
export function RequesterShell({ children }: { children: ReactNode }) {
  const { user, userIsLoading } = useAuth()
  const router = useRouter()

  useEffect(() => {
    if (userIsLoading || user) {
      return
    }
    const returnUrl = `${window.location.pathname}${window.location.search}`
    router.replace(`/sign-in?returnUrl=${encodeURIComponent(returnUrl)}`)
  }, [user, userIsLoading, router])

  let content: ReactNode = null
  if (userIsLoading) {
    content = <CenteredSpinner />
  } else if (user) {
    content = <RequesterGate>{children}</RequesterGate>
  }
  return <div className={requesterStyles.frame}>{content}</div>
}

/** Loading, failure, empty and "Load more" around a requester list. */
function RequesterList<T>({
  list,
  errorTitle,
  empty,
  children,
}: {
  list: CursorList<T>
  errorTitle: string
  empty: { icon: LucideIcon; title: string; description: string }
  children: (items: T[]) => ReactNode
}) {
  if (list.isLoading) {
    return <CenteredSpinner />
  }
  if (list.error) {
    return <RetryState title={errorTitle} onRetry={() => list.refetch()} />
  }
  if (list.items.length === 0) {
    return <RequesterState {...empty} />
  }
  return (
    <div className="flex flex-col gap-6">
      {children(list.items)}
      {list.hasNextPage && (
        <Button
          variant="outline"
          className={cn(formStyles.button, "self-center")}
          disabled={list.isFetchingNextPage}
          onClick={() => list.fetchNextPage()}
        >
          {list.isFetchingNextPage ? "Loading..." : "Load more"}
        </Button>
      )}
    </div>
  )
}

/** The forms a requester can fill in, grouped by workspace in server order. */
function RequesterCatalog() {
  const list = useRequesterForms()
  return (
    <RequesterList
      list={list}
      errorTitle="Couldn't load forms"
      empty={{
        icon: ClipboardListIcon,
        title: "No forms yet",
        description: "Forms you can fill in will show up here.",
      }}
    >
      {(forms) => {
        const groups = new Map<string, FormIntakeRead[]>()
        for (const form of forms) {
          const group = groups.get(form.workspace_id) ?? []
          groups.set(form.workspace_id, [...group, form])
        }
        return [...groups.values()].map((group) => (
          <section key={group[0].workspace_id}>
            <h2 className="mb-2 text-xs font-medium text-muted-foreground">
              {group[0].workspace_name}
            </h2>
            <ul className={requesterStyles.list}>
              {group.map((form) => (
                <li key={form.id} className="border-b last:border-b-0">
                  <Link
                    href={`/forms/${form.id}`}
                    className="flex items-center gap-3 p-3 hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:outline-none"
                  >
                    <span className="flex size-8 flex-none items-center justify-center rounded-md bg-muted">
                      <ClipboardListIcon className="size-4" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className={cn(formStyles.label, "block truncate")}>
                        {form.title}
                      </span>
                      {form.description && (
                        <span className={cn(formStyles.help, "block truncate")}>
                          {formMarkdownToPlainText(form.description)}
                        </span>
                      )}
                    </span>
                    <ChevronRightIcon className="size-4 flex-none text-muted-foreground" />
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        ))
      }}
    </RequesterList>
  )
}

/** The requester's own submissions, without the run they started. */
function RequesterSubmissions() {
  const list = useRequesterSubmissions()
  return (
    <RequesterList
      list={list}
      errorTitle="Couldn't load your submissions"
      empty={{
        icon: InboxIcon,
        title: "No submissions yet",
        description: "Forms you submit will show up here.",
      }}
    >
      {(submissions) => (
        <div className={cn(requesterStyles.list, "overflow-x-auto")}>
          <Table className="text-sm">
            <TableHeader>
              <TableRow>
                <TableHead>Form</TableHead>
                <TableHead>Reference</TableHead>
                <TableHead>Submitted</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {submissions.map((submission) => (
                <TableRow key={submission.id}>
                  <TableCell>
                    <span className="font-medium">{submission.form_title}</span>{" "}
                    <span className={formStyles.help}>
                      {submission.workspace_name}
                    </span>
                  </TableCell>
                  <TableCell className="font-mono text-[13px]">
                    {submission.reference}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-muted-foreground">
                    {format(
                      new Date(submission.submitted_at),
                      "MMM d, yyyy, p"
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </RequesterList>
  )
}

/**
 * The requester landing page: the catalog, or My submissions under
 * `?tab=submissions`. Render it inside a `Suspense` boundary.
 */
export function RequesterHome() {
  const showSubmissions = useSearchParams().get("tab") === "submissions"
  const tabs = [
    { label: "Forms", href: "/forms", active: !showSubmissions },
    {
      label: "My submissions",
      href: MY_SUBMISSIONS_HREF,
      active: showSubmissions,
    },
  ]

  return (
    <RequesterPage title="Forms">
      <div className={requesterStyles.catalogColumn}>
        <h1 className={requesterStyles.pageTitle}>Forms</h1>
        <nav aria-label="Forms views" className="mt-5 flex gap-5 border-b">
          {tabs.map((tab) => (
            <Link
              key={tab.href}
              href={tab.href}
              aria-current={tab.active ? "page" : undefined}
              className={cn(
                "-mb-px flex h-9 items-center border-b-2 text-sm font-medium",
                tab.active
                  ? "border-primary text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              )}
            >
              {tab.label}
            </Link>
          ))}
        </nav>
        <div className="mt-6">
          {showSubmissions ? <RequesterSubmissions /> : <RequesterCatalog />}
        </div>
      </div>
    </RequesterPage>
  )
}

/**
 * The fill-in page for one form. A requester arrives with only a form ID, so
 * this locates the form, then renders it, then submits to it.
 */
export function RequesterForm({ formId }: { formId: string }) {
  const { user } = useAuth()
  const location = useFormLocation(formId)
  const workspaceId = location.data?.workspace_id
  const {
    data: form,
    isLoading: formIsLoading,
    error: formError,
    refetch,
  } = useRenderedForm({
    workspaceId,
    formId,
  })
  const { userScopes, isLoading: scopesIsLoading } = useUserScopes(
    workspaceId,
    { enabled: Boolean(workspaceId) }
  )
  const { mutateAsync: submitForm } = useSubmitForm({
    workspaceId: workspaceId ?? "",
    formId,
  })
  // Set when a submission finds the form offline after it was rendered.
  const [wentOffline, setWentOffline] = useState(false)

  async function handleSubmit(values: FormValues) {
    try {
      return await submitForm({
        values,
        definition_version: form?.definition_version,
      })
    } catch (error) {
      if (getFormErrorCode(error) === "form_changed") {
        // Load the new version. The renderer keeps the answers that still
        // apply and tells the requester the form was updated.
        await refetch()
      } else if (isFormOfflineError(error)) {
        setWentOffline(true)
      }
      throw error
    }
  }

  let body: ReactNode
  if (location.isLoading) {
    body = <CenteredSpinner />
  } else if (!workspaceId) {
    body =
      getFormLoadFailure(location.error) === "not_found" ? (
        <FormUnavailableState offline={false} />
      ) : (
        <RetryState onRetry={() => location.refetch()} />
      )
  } else if (wentOffline) {
    body = <FormUnavailableState offline />
  } else if (formIsLoading || scopesIsLoading) {
    body = <CenteredSpinner />
  } else if (formError && getFormLoadFailure(formError) !== "error") {
    body = (
      <FormUnavailableState
        offline={getFormLoadFailure(formError) === "offline"}
      />
    )
  } else if (!form) {
    body = <RetryState onRetry={() => refetch()} />
  } else {
    body = (
      <div className={requesterStyles.formColumn}>
        <FormRenderer
          workspaceId={workspaceId}
          formId={formId}
          form={form}
          mode="live"
          // Unknown scopes do not block the form: the server decides.
          canSubmit={
            userScopes
              ? hasGrantedScope("form:submit", userScopes.scopes)
              : true
          }
          onSubmit={handleSubmit}
          uploadFile={uploadFormFile}
          submitterEmail={user?.email}
        />
      </div>
    )
  }

  return (
    <RequesterPage title={location.data?.workspace_name ?? "Forms"}>
      {body}
    </RequesterPage>
  )
}
