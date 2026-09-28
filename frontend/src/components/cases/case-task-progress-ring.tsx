import { cn } from "@/lib/utils"

const RING_RADIUS = 6
const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS

/** Props for {@link CaseTaskProgressRing}. */
export interface CaseTaskProgressRingProps {
  /** Completed task count. */
  done: number
  /** Total task count. Render nothing at the call site when this is zero. */
  total: number
  className?: string
}

/**
 * Fraction-complete arc for the Tasks switcher button. 14px rendered in a
 * 16px viewBox with `r=6`/`strokeWidth=2`, so the stroke's outer edge lands
 * inside the box with no clipping. `stroke-primary` is byte-identical in
 * light and dark and clears the WCAG 1.4.11 3:1 bar for non-text graphics;
 * at `done === total` it stays primary — the figure already communicates
 * completion. Decorative (`aria-hidden`): the owning button carries the
 * accessible name and count.
 */
export function CaseTaskProgressRing({
  done,
  total,
  className,
}: CaseTaskProgressRingProps) {
  const fraction = total > 0 ? Math.min(Math.max(done / total, 0), 1) : 0
  return (
    <svg
      viewBox="0 0 16 16"
      className={cn("size-3.5 shrink-0", className)}
      aria-hidden="true"
    >
      <circle
        cx="8"
        cy="8"
        r={RING_RADIUS}
        fill="none"
        strokeWidth="2"
        className="stroke-muted-foreground/30"
      />
      {/* rotate(-90) starts the arc at 12 o'clock. Skipped at zero: a
          zero-length dash with round linecaps still paints a stray dot. */}
      {fraction > 0 && (
        <circle
          cx="8"
          cy="8"
          r={RING_RADIUS}
          fill="none"
          strokeWidth="2"
          strokeLinecap="round"
          strokeDasharray={RING_CIRCUMFERENCE}
          strokeDashoffset={RING_CIRCUMFERENCE * (1 - fraction)}
          transform="rotate(-90 8 8)"
          className="stroke-primary"
        />
      )}
    </svg>
  )
}
