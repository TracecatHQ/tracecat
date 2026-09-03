import { runWithConcurrencyLimit } from "@/lib/utils"

function createDeferred(): { promise: Promise<void>; resolve: () => void } {
  let resolve!: () => void
  const promise = new Promise<void>((fulfill) => {
    resolve = fulfill
  })
  return { promise, resolve }
}

describe("runWithConcurrencyLimit", () => {
  it("limits tasks in flight and preserves input order", async () => {
    let inFlight = 0
    let maxInFlight = 0
    const completionDelays = [30, 5, 20, 1]

    const results = await runWithConcurrencyLimit(
      completionDelays.map(
        (delay, index) => () =>
          new Promise<number>((resolve) => {
            inFlight += 1
            maxInFlight = Math.max(maxInFlight, inFlight)
            setTimeout(() => {
              inFlight -= 1
              resolve(index)
            }, delay)
          })
      ),
      2
    )

    expect(maxInFlight).toBe(2)
    expect(results).toEqual([0, 1, 2, 3])
  })

  it("stops scheduling after the first failure and awaits in-flight tasks", async () => {
    const firstError = new Error("first failure")
    const started: number[] = []
    const inFlightTask = createDeferred()
    const failingTask = createDeferred()

    const runPromise = runWithConcurrencyLimit(
      [
        async () => {
          started.push(0)
          await inFlightTask.promise
        },
        async () => {
          started.push(1)
          await failingTask.promise
          throw firstError
        },
        async () => {
          started.push(2)
        },
      ],
      2
    )

    expect(started).toEqual([0, 1])
    failingTask.resolve()
    await Promise.resolve()
    await Promise.resolve()

    expect(started).toEqual([0, 1])

    let settled = false
    void runPromise.then(
      () => {
        settled = true
      },
      () => {
        settled = true
      }
    )
    await Promise.resolve()
    expect(settled).toBe(false)

    inFlightTask.resolve()
    await expect(runPromise).rejects.toBe(firstError)
  })
})
