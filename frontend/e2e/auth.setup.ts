import { test as setup } from "@playwright/test"

import {
  signInRequest,
  TENANT_USER_EMAIL,
  TENANT_USER_PASSWORD,
} from "./utils/auth"

const authFile = "e2e/.auth/dev-user.json"

setup("authenticate seeded tenant user", async ({ request }) => {
  await signInRequest(request, TENANT_USER_EMAIL, TENANT_USER_PASSWORD)
  await request.storageState({ path: authFile })
})
