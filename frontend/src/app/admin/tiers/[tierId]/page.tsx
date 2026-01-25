"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { ArrowLeftIcon } from "lucide-react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { use, useEffect } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
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
import { useAdminTier } from "@/hooks/use-admin"

const formSchema = z.object({
  display_name: z
    .string()
    .min(1, "Name is required")
    .max(50, "Name is too long"),
  max_concurrent_workflows: z.coerce
    .number()
    .int()
    .positive()
    .optional()
    .nullable(),
  max_action_executions_per_workflow: z.coerce
    .number()
    .int()
    .positive()
    .optional()
    .nullable(),
  max_concurrent_actions: z.coerce
    .number()
    .int()
    .positive()
    .optional()
    .nullable(),
  api_rate_limit: z.coerce.number().int().positive().optional().nullable(),
  api_burst_capacity: z.coerce.number().int().positive().optional().nullable(),
  is_default: z.boolean(),
  is_active: z.boolean(),
  sort_order: z.coerce.number().int().min(0),
})

type FormValues = z.infer<typeof formSchema>

export default function AdminTierDetailPage({
  params,
}: {
  params: Promise<{ tierId: string }>
}) {
  const router = useRouter()
  const { tierId } = use(params)
  const { tier, isLoading, updateTier, updatePending } = useAdminTier(tierId)

  const form = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      display_name: "",
      is_default: false,
      is_active: true,
      sort_order: 0,
    },
  })

  useEffect(() => {
    if (tier) {
      form.reset({
        display_name: tier.display_name,
        max_concurrent_workflows: tier.max_concurrent_workflows,
        max_action_executions_per_workflow:
          tier.max_action_executions_per_workflow,
        max_concurrent_actions: tier.max_concurrent_actions,
        api_rate_limit: tier.api_rate_limit,
        api_burst_capacity: tier.api_burst_capacity,
        is_default: tier.is_default,
        is_active: true, // TierRead doesn't have is_active, default to true
        sort_order: tier.sort_order,
      })
    }
  }, [tier, form])

  const onSubmit = async (values: FormValues) => {
    try {
      await updateTier({
        display_name: values.display_name,
        max_concurrent_workflows: values.max_concurrent_workflows ?? null,
        max_action_executions_per_workflow:
          values.max_action_executions_per_workflow ?? null,
        max_concurrent_actions: values.max_concurrent_actions ?? null,
        api_rate_limit: values.api_rate_limit ?? null,
        api_burst_capacity: values.api_burst_capacity ?? null,
        is_default: values.is_default,
        is_active: values.is_active,
        sort_order: values.sort_order,
      })
      toast({
        title: "Tier updated",
        description: "Changes have been saved.",
      })
      router.push("/admin/tiers")
    } catch (error) {
      console.error("Failed to update tier", error)
      toast({
        title: "Failed to update tier",
        description: "Please try again.",
        variant: "destructive",
      })
    }
  }

  if (isLoading) {
    return <div className="text-center text-muted-foreground">Loading...</div>
  }

  if (!tier) {
    return (
      <div className="text-center text-muted-foreground">Tier not found</div>
    )
  }

  return (
    <div className="space-y-8">
      <div>
        <Link
          href="/admin/tiers"
          className="flex items-center text-sm text-muted-foreground hover:text-foreground mb-4"
        >
          <ArrowLeftIcon className="mr-2 size-4" />
          Back to tiers
        </Link>
        <h1 className="text-2xl font-semibold tracking-tight">Edit tier</h1>
        <p className="text-muted-foreground">
          Update tier details for {tier.display_name}.
        </p>
      </div>

      <Form {...form}>
        <form
          onSubmit={form.handleSubmit(onSubmit)}
          className="space-y-6 max-w-2xl"
        >
          <div className="grid grid-cols-2 gap-4">
            <FormField
              control={form.control}
              name="display_name"
              render={({ field }) => (
                <FormItem className="col-span-2">
                  <FormLabel>Name</FormLabel>
                  <FormControl>
                    <Input {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="sort_order"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Sort order</FormLabel>
                  <FormControl>
                    <Input type="number" min={0} {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <div className="space-y-4 pt-6">
              <FormField
                control={form.control}
                name="is_default"
                render={({ field }) => (
                  <FormItem className="flex items-center gap-2 space-y-0">
                    <FormControl>
                      <Checkbox
                        checked={field.value}
                        onCheckedChange={field.onChange}
                      />
                    </FormControl>
                    <FormLabel className="font-normal">Default tier</FormLabel>
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="is_active"
                render={({ field }) => (
                  <FormItem className="flex items-center gap-2 space-y-0">
                    <FormControl>
                      <Checkbox
                        checked={field.value}
                        onCheckedChange={field.onChange}
                      />
                    </FormControl>
                    <FormLabel className="font-normal">Active</FormLabel>
                  </FormItem>
                )}
              />
            </div>
          </div>

          <div className="border-t pt-6">
            <h4 className="text-sm font-medium mb-4">Resource limits</h4>
            <div className="grid grid-cols-2 gap-4">
              <FormField
                control={form.control}
                name="max_concurrent_workflows"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Max concurrent workflows</FormLabel>
                    <FormControl>
                      <Input
                        type="number"
                        placeholder="Unlimited"
                        {...field}
                        value={field.value ?? ""}
                        onChange={(e) =>
                          field.onChange(
                            e.target.value === ""
                              ? null
                              : Number(e.target.value)
                          )
                        }
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="max_action_executions_per_workflow"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Max actions per workflow</FormLabel>
                    <FormControl>
                      <Input
                        type="number"
                        placeholder="Unlimited"
                        {...field}
                        value={field.value ?? ""}
                        onChange={(e) =>
                          field.onChange(
                            e.target.value === ""
                              ? null
                              : Number(e.target.value)
                          )
                        }
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="max_concurrent_actions"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Max concurrent actions</FormLabel>
                    <FormControl>
                      <Input
                        type="number"
                        placeholder="Unlimited"
                        {...field}
                        value={field.value ?? ""}
                        onChange={(e) =>
                          field.onChange(
                            e.target.value === ""
                              ? null
                              : Number(e.target.value)
                          )
                        }
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="api_rate_limit"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>API rate limit</FormLabel>
                    <FormControl>
                      <Input
                        type="number"
                        placeholder="Unlimited"
                        {...field}
                        value={field.value ?? ""}
                        onChange={(e) =>
                          field.onChange(
                            e.target.value === ""
                              ? null
                              : Number(e.target.value)
                          )
                        }
                      />
                    </FormControl>
                    <FormDescription>Requests per second</FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="api_burst_capacity"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>API burst capacity</FormLabel>
                    <FormControl>
                      <Input
                        type="number"
                        placeholder="Unlimited"
                        {...field}
                        value={field.value ?? ""}
                        onChange={(e) =>
                          field.onChange(
                            e.target.value === ""
                              ? null
                              : Number(e.target.value)
                          )
                        }
                      />
                    </FormControl>
                    <FormDescription>Maximum burst requests</FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>
          </div>

          <div className="flex gap-4 pt-4">
            <Button type="submit" disabled={updatePending}>
              {updatePending ? "Saving..." : "Save changes"}
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={() => router.push("/admin/tiers")}
            >
              Cancel
            </Button>
          </div>
        </form>
      </Form>
    </div>
  )
}
