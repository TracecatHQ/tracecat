interface SettingsHeaderProps {
  title: string
  description: string
}

export function SettingsHeader({ title, description }: SettingsHeaderProps) {
  return (
    <div className="flex w-full">
      <div className="items-start space-y-3 text-left">
        <h2 className="text-2xl font-semibold tracking-tight">{title}</h2>
        <p className="text-md text-muted-foreground">{description}</p>
      </div>
    </div>
  )
}
