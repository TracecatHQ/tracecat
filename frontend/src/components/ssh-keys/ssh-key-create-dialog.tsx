"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import type { DialogProps } from "@radix-ui/react-dialog"
import { KeyRoundIcon } from "lucide-react"
import React from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { SecretCreate } from "@/client"
import { sshKeyRegex } from "@/components/ssh-keys/ssh-key-utils"
import { SshPrivateKeyField } from "@/components/ssh-keys/ssh-private-key-field"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
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

interface FieldConfig {
  defaultValue?: string
  disabled?: boolean
}

interface CreateSSHKeyDialogProps
  extends DialogProps,
    React.HTMLAttributes<HTMLDivElement> {
  children?: React.ReactNode
  handler: (params: SecretCreate) => void
  fieldConfig?: {
    name?: FieldConfig
    description?: FieldConfig
    environment?: FieldConfig
  }
}
const createSSHKeySchema = z.object({
  name: z.string().default(""),
  description: z.string().max(255).default(""),
  environment: z
    .string()
    .nullable()
    .transform((val) => val || "default"), // "default" if null or empty
  private_key: z
    .string()
    .min(1, "SSH private key is required")
    .refine(
      (key) => sshKeyRegex.test(key),
      "Invalid SSH private key format. Must be in PEM format with proper header and footer."
    ),
})
type CreateSSHKeyForm = z.infer<typeof createSSHKeySchema>

export function CreateSSHKeyDialog({
  children,
  className,
  handler,
  fieldConfig,
}: CreateSSHKeyDialogProps) {
  const [showDialog, setShowDialog] = React.useState(false)

  const methods = useForm<CreateSSHKeyForm>({
    mode: "onChange",
    resolver: zodResolver(createSSHKeySchema),
    defaultValues: {
      name: fieldConfig?.name?.defaultValue || "",
      description: fieldConfig?.description?.defaultValue || "",
      environment: fieldConfig?.environment?.defaultValue || "",
      private_key: "",
    },
  })
  const { control, register } = methods

  const onSubmit = async (values: CreateSSHKeyForm) => {
    const { private_key, ...rest } = values
    try {
      const secret: SecretCreate = {
        type: "ssh-key",
        keys: [{ key: "PRIVATE_KEY", value: private_key }],
        ...rest,
      }
      await handler(secret)
    } catch (error) {
      console.error(error)
    }
    methods.reset()
  }
  const onValidationFailed = () => {
    console.error("Form validation failed")
    toast({
      title: "Form validation failed",
      description: "A validation error occurred while adding the new secret.",
    })
  }
  return (
    <Dialog open={showDialog} onOpenChange={setShowDialog}>
      {children}
      <DialogContent className={className}>
        <DialogHeader>
          <DialogTitle>Create new SSH key</DialogTitle>
          <div className="flex text-sm leading-relaxed text-muted-foreground">
            <span>
              Create a new SSH key that can be used to authenticate into your
              private actions registry.
            </span>
          </div>
        </DialogHeader>
        <Form {...methods}>
          <form onSubmit={methods.handleSubmit(onSubmit, onValidationFailed)}>
            <div className="space-y-4">
              <FormField
                key="name"
                control={control}
                name="name"
                render={() => (
                  <FormItem>
                    <FormLabel className="text-sm">Name</FormLabel>
                    <FormControl>
                      <Input
                        className="text-sm"
                        placeholder="Name (snake case)"
                        disabled={fieldConfig?.name?.disabled}
                        {...register("name")}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                key="description"
                control={control}
                name="description"
                render={() => (
                  <FormItem>
                    <FormLabel className="text-sm">Description</FormLabel>
                    <FormDescription className="text-sm">
                      A description for this secret.
                    </FormDescription>
                    <FormControl>
                      <Input
                        className="text-sm"
                        placeholder="Description"
                        disabled={fieldConfig?.description?.disabled}
                        {...register("description")}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                key="environment"
                control={control}
                name="environment"
                render={() => (
                  <FormItem>
                    <FormLabel className="text-sm">Environment</FormLabel>
                    <FormDescription className="text-sm">
                      The workflow&apos;s target execution environment.
                    </FormDescription>
                    <FormControl>
                      <Input
                        className="text-sm"
                        placeholder='Default environment: "default"'
                        disabled={fieldConfig?.environment?.disabled}
                        {...register("environment")}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <SshPrivateKeyField
                control={control}
                register={register}
                name="private_key"
              />
              <DialogFooter>
                <DialogClose asChild>
                  <Button className="ml-auto space-x-2" type="submit">
                    <KeyRoundIcon className="mr-2 size-4" />
                    Create SSH key
                  </Button>
                </DialogClose>
              </DialogFooter>
            </div>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}

export const CreateSSHKeyDialogTrigger = DialogTrigger
