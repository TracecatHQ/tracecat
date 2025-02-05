import { ExternalLinkIcon } from "lucide-react"

export function RunIfTooltip() {
  return (
    <div className="w-full space-y-4">
      <div className="flex w-full items-center justify-between text-muted-foreground ">
        <span className="font-mono text-sm font-semibold">run_if</span>
        <span className="text-xs text-muted-foreground/80">(optional)</span>
      </div>
      <div className="flex w-full items-center justify-between text-muted-foreground ">
        <span>
          A run-if expression is a conditional expression that evaluates to a
          truthy or falsy value:
        </span>
      </div>
      <div className="rounded-md border bg-muted-foreground/10 p-2">
        <pre className="text-xs text-foreground/70">{"${{ <condition> }}"}</pre>
      </div>
      <div className="w-full items-center text-start">
        <span>Example inputs: </span>
      </div>
      <div className="flex w-full flex-col space-y-2 text-muted-foreground">
        <div className="rounded-md border bg-muted-foreground/10 p-2">
          <pre className="text-xs text-foreground/70">
            {"${{ FN.not_empty(ACTIONS.my_action.result) }}"}
          </pre>
        </div>
        <div className="rounded-md border bg-muted-foreground/10 p-2">
          <pre className="text-xs text-foreground/70">
            {"${{ ACTIONS.my_action.result.value > 5 }}"}
          </pre>
        </div>
      </div>
    </div>
  )
}

export function ForEachTooltip() {
  return (
    <div className="w-full space-y-4">
      <div className="flex w-full items-center justify-between text-muted-foreground ">
        <span className="font-mono text-sm font-semibold">for_each</span>
        <span className="text-xs text-muted-foreground/80">(optional)</span>
      </div>
      <div className="flex w-full items-center justify-between text-muted-foreground ">
        <span>A loop expression has the form:</span>
      </div>
      <div className="rounded-md border bg-muted-foreground/10 p-2">
        <pre className="text-xs text-foreground/70">
          {"${{ for var.item in <collection> }}"}
        </pre>
      </div>
      <div className="w-full items-center text-start text-muted-foreground ">
        <span>
          Here, `var.item` references an item in the collection, and is local to
          a single loop iteration. This is synonymous to assigning a loop
          variable.
        </span>
      </div>
      <div className="w-full items-center text-start">
        <span>Example inputs: </span>
      </div>
      <div className="flex w-full flex-col text-muted-foreground ">
        <span className="mt-2">Single expression (string):</span>
        <div className="rounded-md border bg-muted-foreground/10 p-2">
          <pre className="text-xs text-foreground/70">
            {"${{ for var.item in ACTIONS.my_action.result }}"}
          </pre>
        </div>
      </div>
      <div className="w-full text-muted-foreground ">
        <span className="mt-2">
          Multiple expressions (array; zipped/lockstep iteration):
        </span>
        <div className="rounded-md border bg-muted-foreground/10 p-2">
          <pre className="flex flex-col text-xs text-foreground/70">
            <span>
              {"- ${{ for var.first in ACTIONS.first_action.result }}"}
            </span>
            <span>
              {"- ${{ for var.second in ACTIONS.second_action.result }}"}
            </span>
          </pre>
        </div>
      </div>
    </div>
  )
}

export function RetryPolicyTooltip() {
  return (
    <div className="w-full space-y-4">
      <div className="flex w-full items-center justify-between text-muted-foreground ">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm font-semibold">max_attempts</span>
          <span className="text-xs font-normal text-muted-foreground/80">
            integer
          </span>
        </div>
        <span className="text-xs text-muted-foreground/80">(optional)</span>
      </div>
      <div className="w-full items-center space-y-2 text-start text-muted-foreground">
        <div>
          Specifies the maximum number of times an action will be retried upon
          failure.
        </div>
        <div>Defaults to 1.</div>
        <div>
          <b className="text-rose-500">WARNING</b> If this value is 0, the
          action will be retried indefinitely.
        </div>
      </div>
      <div className="flex w-full items-center justify-between text-muted-foreground ">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm font-semibold">timeout</span>
          <span className="text-xs font-normal text-muted-foreground/80">
            integer
          </span>
        </div>
        <span className="text-xs text-muted-foreground/80">(optional)</span>
      </div>
      <div className="w-full items-center space-y-2 text-start text-muted-foreground">
        <div>
          Defines the maximum duration (in seconds) that an action is allowed to
          run before it is terminated. If not specified, the action will run
          until completion or failure.
        </div>
        <div>Defaults to 300s (5 minutes).</div>
      </div>
      <div className="w-full items-center text-start">
        <span>Example inputs: </span>
      </div>
      <div className="flex w-full flex-col text-muted-foreground">
        <div className="rounded-md border bg-muted-foreground/10 p-2">
          <pre className="text-xs text-foreground/70">
            {"max_attempts: 5\ntimeout: 300  # 5 minutes"}
          </pre>
        </div>
      </div>
    </div>
  )
}

export function ControlFlowOptionsTooltip() {
  return (
    <div className="w-full space-y-4">
      <div className="flex w-full items-center justify-between text-muted-foreground ">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm font-semibold">start_delay</span>
          <span className="text-xs font-normal text-muted-foreground/80">
            float
          </span>
        </div>
        <span className="text-xs text-muted-foreground/80">(optional)</span>
      </div>
      <div className="w-full items-center space-y-2 text-start text-muted-foreground">
        <div>Specifies the delay (in seconds) before starting the action.</div>
        <div>Defaults to 0.0 seconds.</div>
      </div>
      <div className="flex w-full items-center justify-between text-muted-foreground ">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm font-semibold">join_strategy</span>
          <span className="text-xs font-normal text-muted-foreground/80">
            string
          </span>
        </div>
        <span className="text-xs text-muted-foreground/80">(optional)</span>
      </div>
      <div className="w-full items-center space-y-2 text-start text-muted-foreground">
        <div>
          Strategy to use when joining multiple branches into this action.
        </div>
        <div>
          Can be either `all` (wait for all branches) or `any` (wait for any
          branch).
        </div>
        <div>Defaults to `all`.</div>
      </div>
      <div className="flex w-full items-center justify-between text-muted-foreground ">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm font-semibold">wait_until</span>
          <span className="text-xs font-normal text-muted-foreground/80">
            string
          </span>
        </div>
        <span className="text-xs text-muted-foreground/80">(optional)</span>
      </div>
      <div className="w-full items-center space-y-2 text-start text-muted-foreground">
        <div>
          Specifies when to start the action using natural language or datetime
          strings. We use the Python `dateparser` library to parse these
          strings.
        </div>
        <div>Supports various formats including:</div>
        <ul className="list-disc pl-4 text-xs">
          <li>
            Natural language: &quot;tomorrow at 3pm&quot;, &quot;in 2
            hours&quot;
          </li>
          <li>Relative: &quot;3 days&quot;, &quot;2 weeks from now&quot;</li>
          <li>
            Absolute: &quot;2024-03-21 15:30:00&quot;, &quot;March 21st
            3:30pm&quot;
          </li>
        </ul>
        {/* docs link */}
        <a
          href="https://dateparser.readthedocs.io/en/latest/"
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-1 text-blue-500 hover:text-blue-600 hover:underline"
        >
          <span>View dateparser documentation</span>
          <ExternalLinkIcon className="size-3" />
        </a>
      </div>
      <div className="w-full items-center text-start">
        <span>Example inputs: </span>
      </div>
      <div className="flex w-full flex-col space-y-2 text-muted-foreground">
        <div className="rounded-md border bg-muted-foreground/10 p-2">
          <pre className="text-xs text-foreground/70">
            <p>
              <strong>wait_until: tomorrow at 3pm</strong>
            </p>
          </pre>
        </div>
        <div className="rounded-md border bg-muted-foreground/10 p-2">
          <pre className="text-xs text-foreground/70">
            <p>
              <strong>wait_until: in 2 hours</strong>
            </p>
          </pre>
        </div>
        <div className="rounded-md border bg-muted-foreground/10 p-2">
          <pre className="text-xs text-foreground/70">
            <p>
              <strong>start_delay: 1.5</strong> # 1.5 seconds
            </p>
            <p>
              <strong>wait_until: 2024-03-21 15:30:00</strong> # specific
              datetime
            </p>
          </pre>
        </div>
      </div>
    </div>
  )
}
