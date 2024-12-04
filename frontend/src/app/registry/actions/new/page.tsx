"use client"

import React from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { TemplateAction_Output, TemplateActionDefinition } from "@/client"
import { zodResolver } from "@hookform/resolvers/zod"
import { AlertTriangleIcon, ArrowLeftIcon, Loader2 } from "lucide-react"
import { Controller, useForm } from "react-hook-form"
import YAML from "yaml"
import { z } from "zod"

import { TracecatApiError } from "@/lib/errors"
import {
  useRegistryAction,
  useRegistryActions,
  useRegistryRepositories,
} from "@/lib/hooks"
import { isTemplateAction } from "@/lib/registry"
import { itemOrEmptyString } from "@/lib/utils"
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
import { Button } from "@/components/ui/button"
import { Form, FormMessage } from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { DynamicCustomEditor } from "@/components/editor/dynamic"
import { CenteredSpinner } from "@/components/loading/spinner"

export default function NewActionPage() {
  const searchParams = useSearchParams()
  const origin = searchParams.get("origin")
  const actionName = searchParams.get("template")

  if (!actionName) {
    return <div>No template action name provided</div>
  }
  if (!origin) {
    return <div>No origin provided</div>
  }

  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12 p-16">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <Breadcrumb>
              <BreadcrumbList>
                <BreadcrumbItem>
                  <BreadcrumbLink
                    href="/registry/actions"
                    className="flex items-center"
                  >
                    <ArrowLeftIcon className="mr-2 size-4" />
                    Registry
                  </BreadcrumbLink>
                </BreadcrumbItem>
                <BreadcrumbSeparator>{"/"}</BreadcrumbSeparator>
                <BreadcrumbItem>
                  <BreadcrumbLink>New Action</BreadcrumbLink>
                </BreadcrumbItem>
              </BreadcrumbList>
            </Breadcrumb>

            <h2 className="text-2xl font-semibold tracking-tight">
              New Registry Action
            </h2>
            <p className="text-md text-muted-foreground">
              Create a new action from an existing template.
            </p>
          </div>
        </div>
        <NewTemplateActionView actionName={actionName} origin={origin} />
      </div>
    </div>
  )
}

function NewTemplateActionView({
  actionName,
  origin,
}: {
  actionName: string
  origin: string
}) {
  const { registryAction, registryActionIsLoading, registryActionError } =
    useRegistryAction(actionName, origin)

  const { registryRepos } = useRegistryRepositories()

  if (registryActionIsLoading || !registryAction) {
    return <CenteredSpinner />
  }

  if (registryActionError) {
    return <div>Error: {registryActionError.message}</div>
  }

  if (!isTemplateAction(registryAction?.implementation)) {
    return <div>Error: Action is not a template</div>
  }

  const repo = registryRepos?.find((r) => r.origin === origin)

  if (!repo) {
    return <div>Error: Repository {origin} not found</div>
  }

  return (
    <NewTemplateActionForm
      origin={origin}
      repositoryId={repo.id}
      baseTemplateAction={registryAction.implementation.template_action}
    />
  )
}

const newTemplateActionFormSchema = z.object({
  origin: z.string(),
  definition: z.string(),
})

type NewTemplateActionFormSchema = z.infer<typeof newTemplateActionFormSchema>

function NewTemplateActionForm({
  repositoryId,
  origin,
  baseTemplateAction,
}: {
  repositoryId: string
  origin: string
  baseTemplateAction: TemplateAction_Output
}) {
  const [error, setError] = React.useState<string | null>(null)
  const router = useRouter()
  const { createRegistryAction, createRegistryActionIsPending } =
    useRegistryActions()

  const methods = useForm<NewTemplateActionFormSchema>({
    resolver: zodResolver(newTemplateActionFormSchema),
    defaultValues: {
      origin,
      definition: itemOrEmptyString(baseTemplateAction.definition),
    },
  })

  const onSubmit = async (data: NewTemplateActionFormSchema) => {
    setError(null)
    console.log("Form submitted:", data)
    try {
      const defn = YAML.parse(data.definition) as TemplateActionDefinition
      await createRegistryAction({
        repository_id: repositoryId,
        name: defn.name,
        type: "template",
        description: defn.description || "",
        namespace: defn.namespace,
        origin: data.origin,
        default_title: defn.title,
        display_group: defn.display_group,
        secrets: defn.secrets || null,
        interface: {
          expects: defn.expects,
          returns: defn.returns,
        },
        implementation: {
          type: "template",
          template_action: {
            definition: defn,
          },
        },
      })
      router.push("/registry/actions")
    } catch (error) {
      const apiError = error as TracecatApiError
      setError(String(apiError.body.detail))
    }
  }

  return (
    <Form {...methods}>
      <form onSubmit={methods.handleSubmit(onSubmit)} className="space-y-8">
        <div className="space-y-4">
          <Controller
            name="origin"
            control={methods.control}
            render={({ field }) => (
              <div>
                <Label htmlFor="origin">Origin</Label>
                <Input
                  disabled
                  id="version"
                  placeholder="Enter version"
                  className="font-mono"
                  {...field}
                />
              </div>
            )}
          />

          <div className="flex flex-col space-y-4">
            <span className="text-xs text-muted-foreground">
              Edit the action template in YAML.
            </span>
            <Controller
              name="definition"
              control={methods.control}
              render={({ field }) => (
                <DynamicCustomEditor
                  className="h-96 w-full"
                  defaultLanguage="yaml-extended"
                  value={field.value}
                  onChange={field.onChange}
                />
              )}
            />
            {error && (
              <FormMessage className="flex items-center space-x-1">
                <AlertTriangleIcon className="size-4 fill-red-500 stroke-white" />
                <span>{error}</span>
              </FormMessage>
            )}
          </div>
        </div>

        <Button type="submit" disabled={createRegistryActionIsPending}>
          {createRegistryActionIsPending ? (
            <>
              <Loader2 className="mr-2 size-4 animate-spin" />
              Creating...
            </>
          ) : (
            "Create Action"
          )}
        </Button>
      </form>
    </Form>
  )
}
