"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useEffect } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { toast } from "@/components/ui/use-toast"
import { useAdminRegistrySettings } from "@/hooks/use-admin"

const formSchema = z.object({
  git_repo_url: z.string().url().optional().nullable().or(z.literal("")),
  git_repo_package_name: z.string().optional().nullable().or(z.literal("")),
  git_allowed_domains: z.string().optional().nullable().or(z.literal("")),
})

type FormValues = z.infer<typeof formSchema>

export function PlatformRegistrySettings() {
  const { settings, isLoading, updateSettings, updatePending } =
    useAdminRegistrySettings()

  const form = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      git_repo_url: "",
      git_repo_package_name: "",
      git_allowed_domains: "",
    },
  })

  useEffect(() => {
    if (settings) {
      form.reset({
        git_repo_url: settings.git_repo_url ?? "",
        git_repo_package_name: settings.git_repo_package_name ?? "",
        git_allowed_domains: settings.git_allowed_domains?.join(", ") ?? "",
      })
    }
  }, [settings, form])

  const onSubmit = async (values: FormValues) => {
    try {
      await updateSettings({
        git_repo_url: values.git_repo_url || null,
        git_repo_package_name: values.git_repo_package_name || null,
        git_allowed_domains: values.git_allowed_domains
          ? values.git_allowed_domains.split(",").map((d) => d.trim())
          : null,
      })
      toast({
        title: "Settings updated",
        description: "Registry settings have been saved.",
      })
    } catch (error) {
      console.error("Failed to update settings", error)
      toast({
        title: "Failed to update settings",
        description: "Please try again.",
        variant: "destructive",
      })
    }
  }

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Registry settings</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-muted-foreground">Loading...</div>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Registry settings</CardTitle>
        <CardDescription>
          Configure platform-wide registry settings for git integration.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <FormField
              control={form.control}
              name="git_repo_url"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Git repository URL</FormLabel>
                  <FormControl>
                    <Input
                      placeholder="https://github.com/org/repo"
                      {...field}
                      value={field.value ?? ""}
                    />
                  </FormControl>
                  <FormDescription>
                    Default git repository URL for the registry.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="git_repo_package_name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Package name</FormLabel>
                  <FormControl>
                    <Input
                      placeholder="my-registry"
                      {...field}
                      value={field.value ?? ""}
                    />
                  </FormControl>
                  <FormDescription>
                    Package name within the git repository.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="git_allowed_domains"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Allowed domains</FormLabel>
                  <FormControl>
                    <Input
                      placeholder="github.com, gitlab.com"
                      {...field}
                      value={field.value ?? ""}
                    />
                  </FormControl>
                  <FormDescription>
                    Comma-separated list of allowed git domains.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <Button type="submit" disabled={updatePending}>
              {updatePending ? "Saving..." : "Save settings"}
            </Button>
          </form>
        </Form>
      </CardContent>
    </Card>
  )
}
