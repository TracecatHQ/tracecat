import { defineConfig } from "@hey-api/openapi-ts"

export default defineConfig({
  client: "axios",
  input: "http://localhost:8000/openapi.json",
  output: {
    format: "prettier",
    lint: "eslint",
    path: "./src/client",
  },
})
