"use client"

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"

export default function AdminSettingsPage() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              Platform settings
            </h2>
            <p className="text-md text-muted-foreground">
              Configure global platform settings.
            </p>
          </div>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>General settings</CardTitle>
            <CardDescription>
              Platform-wide configuration options.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground">
              Platform settings configuration coming soon.
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
