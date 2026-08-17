import { defineConfig } from "@hey-api/openapi-ts";

export default defineConfig({
  input: "./openapi.json",
  output: "./src/client",
  plugins: [
    { name: "@hey-api/client-axios", throwOnError: true },
    { name: "@hey-api/typescript", case: "preserve" },
    {
      name: "@hey-api/sdk",
      operations: { strategy: "byTags", methods: "static", containerName: "{{name}}Service" },
    },
  ],
});
