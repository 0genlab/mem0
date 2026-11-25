import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import type MemoryClient from "mem0ai";
import type { Mem0Config, ScopeContext } from "../types.ts";
import { DEFAULT_CUSTOM_CATEGORIES } from "../types.ts";
import { resolveAddParams } from "../memory/scoping.ts";
import { createMemoryLifecycle } from "../../../agent-plugin-core/typescript/src/lifecycle.ts";

export { extractConversation } from "../../../agent-plugin-core/typescript/src/lifecycle.ts";

export function setupAutoCapture(
  pi: ExtensionAPI,
  mem0: MemoryClient,
  config: Mem0Config,
  getScopeCtx: () => ScopeContext,
  lifecycle: ReturnType<typeof createMemoryLifecycle> = createMemoryLifecycle(),
): void {
  if (!config.autoCapture) return;

  pi.on("agent_end", async (event) => {
    const messages = event.messages ?? [];
    const conversation = lifecycle.prepareConversation(messages);
    if (conversation.length === 0) return;

    const scopeCtx = getScopeCtx();
    const addParams = resolveAddParams("project", scopeCtx);

    try {
      await mem0.add(conversation, {
        ...addParams,
        customCategories: DEFAULT_CUSTOM_CATEGORIES,
      });
    } catch (err: unknown) {
      console.error("[mem0] auto-capture failed:", err);
    }
  });
}
