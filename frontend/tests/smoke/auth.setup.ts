import { mkdir } from "node:fs/promises"
import { dirname } from "node:path"
import { test as setup } from "@playwright/test"

import {
  signInRequest,
  TENANT_USER_EMAIL,
  TENANT_USER_PASSWORD,
} from "./utils/auth"

const authFile = "tests/smoke/.auth/dev-user.json"

setup("authenticate seeded tenant user", async ({ request }) => {
  await signInRequest(request, TENANT_USER_EMAIL, TENANT_USER_PASSWORD)
  await mkdir(dirname(authFile), { recursive: true })
  await request.storageState({ path: authFile })
})
