import { Type } from "@sinclair/typebox";
import type { ToolDeps } from "./index.ts";

export function createMemoryGetTool(deps: ToolDeps) {
  const { provider } = deps;

  return {
    name: "memory_get",
    label: "Memory Get",
    description: "Fetch one memory by its exact ID (e.g. an ID returned by memory_search or memory_list) to read its full content and metadata.",
    parameters: Type.Object({
      memoryId: Type.String({ description: "The memory ID to retrieve" }),
    }),

    async execute(_toolCallId: string, params: Record<string, unknown>) {
      const { memoryId } = params as { memoryId: string };
      try {
        const memory = await provider.get(memoryId);
        return {
          content: [{ type: "text", text: `Memory ${memory.id}:\n${memory.memory}\n\nCreated: ${memory.created_at ?? "unknown"}\nUpdated: ${memory.updated_at ?? "unknown"}` }],
          details: { memory },
        };
      } catch (err) {
        return { content: [{ type: "text", text: `Memory get failed: ${String(err)}` }], details: { error: String(err) } };
      }
    },
  };
}
