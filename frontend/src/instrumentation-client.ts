import { config } from "zod/v4/core"

// Zod v4 (used by the AI SDK) probes `Function("")` to enable its JIT parser,
// which the CSP blocks and reports. Run before app code so no schema probes.
config({ jitless: true })
