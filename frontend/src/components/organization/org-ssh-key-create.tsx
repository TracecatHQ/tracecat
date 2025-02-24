"use client"

import React, { PropsWithChildren } from "react"
import { SecretCreate } from "@/client"
import { zodResolver } from "@hookform/resolvers/zod"
import { DialogProps } from "@radix-ui/react-dialog"
import { KeyRoundIcon } from "lucide-react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { useOrgSecrets } from "@/lib/hooks"
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
import { Textarea } from "@/components/ui/textarea"
import { toast } from "@/components/ui/use-toast"

interface CreateOrgSSHKeyDialogProps
  extends PropsWithChildren<
    DialogProps & React.HTMLAttributes<HTMLDivElement>
  > {}
const sshKeyRegex =
  /^-----BEGIN[A-Z\s]+PRIVATE KEY-----\n[A-Za-z0-9+/=\s]+\n-----END[A-Z\s]+PRIVATE KEY-----\n?$/
const createOrgSSHKeySchema = z.object({
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
type CreateOrgSSHKeyForm = z.infer<typeof createOrgSSHKeySchema>

export function CreateOrgSSHKeyDialog({
  children,
  className,
}: CreateOrgSSHKeyDialogProps) {
  const [showDialog, setShowDialog] = React.useState(false)
  const { createSecret } = useOrgSecrets()

  const methods = useForm<CreateOrgSSHKeyForm>({
    mode: "onChange",
    resolver: zodResolver(createOrgSSHKeySchema),
    defaultValues: {
      name: "",
      description: "",
      environment: "",
      private_key: "",
    },
  })
  const { control, register } = methods

  const onSubmit = async (values: CreateOrgSSHKeyForm) => {
    const { private_key, ...rest } = values
    try {
      const secret: SecretCreate = {
        type: "ssh-key",
        keys: [{ key: "PRIVATE_KEY", value: private_key }],
        ...rest,
      }
      await createSecret(secret)
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
                        {...register("environment")}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                key="private_key"
                control={control}
                name="private_key"
                render={() => (
                  <FormItem>
                    <FormLabel className="text-sm">Key</FormLabel>
                    <FormDescription className="text-sm">
                      The SSH private key in PEM format. This is encrypted and
                      stored in Tracecat&apos;s secrets manager.
                    </FormDescription>
                    <FormControl>
                      <Textarea
                        className="h-36 text-sm"
                        placeholder="Starts with '-----BEGIN OPENSSH PRIVATE KEY-----"
                        {...register("private_key")}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
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

export const CreateOrgSSHKeyDialogTrigger = DialogTrigger
