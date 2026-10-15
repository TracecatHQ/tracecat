const OVERFLOW_TOOLTIP_CAP = 10

/** Tooltip body for a `+N` badge: a capped list of names that wrap. */
export function OverflowTooltipList({
  items,
}: {
  items: { key: string; label: string }[]
}) {
  const shown = items.slice(0, OVERFLOW_TOOLTIP_CAP)
  const hiddenCount = items.length - shown.length
  return (
    <div className="max-w-xs">
      <ul className="space-y-0.5">
        {shown.map((item) => (
          <li key={item.key} className="break-words">
            {item.label}
          </li>
        ))}
      </ul>
      {hiddenCount > 0 ? (
        <p className="mt-1 opacity-70">and {hiddenCount} more</p>
      ) : null}
    </div>
  )
}
