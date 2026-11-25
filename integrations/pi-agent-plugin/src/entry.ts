import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import MemoryClient from "mem0ai";
import { loadConfig } from "./config/index.ts";
import { detectAppId, detectRunId, resolveSearchFilters } from "./memory/scoping.ts";
import { registerMemoryTool } from "./memory/tools.ts";
import { registerCommands } from "./commands.ts";
import { setupAutoCapture } from "./capture/index.ts";
import { MEMORY_POLICY } from "./prompt.ts";
import * as os from "node:os";
import type { ScopeContext } from "./types.ts";
import { createMemoryLifecycle } from "../../agent-plugin-core/typescript/src/lifecycle.ts";
import { applySurfaceHeaders } from "./attribution.ts";

export { buildRecallContext } from "../../agent-plugin-core/typescript/src/lifecycle.ts";

export function resolveUserId(configUserId: string): string {
  if (configUserId) return configUserId;
  if (process.env.USER) return process.env.USER;
  if (process.env.USERNAME) return process.env.USERNAME;
  try { return os.userInfo().username; } catch { return "default"; }
}

export default function mem0Extension(pi: ExtensionAPI): void {
  const config = loadConfig();

  if (!config.apiKey) {
    console.warn("[mem0] No API key found. Set MEM0_API_KEY or add apiKey to ~/.pi/agent/mem0-config.json. Extension disabled.");
    return;
  }

  const mem0 = new MemoryClient({ apiKey: config.apiKey });
  // Every path below shares this client: automatic recall, capture, the memory
  // tools and deletion as well as the slash commands. Attribution belongs here
  // rather than on individual calls, or everything except the commands reports
  // as generic SDK traffic.
  applySurfaceHeaders(mem0);

  const scopeCtx: ScopeContext = {
    userId: resolveUserId(config.userId),
    appId: "",
    runId: "unknown",
  };

  function getScopeCtx(): ScopeContext {
    return scopeCtx;
  }

  const lifecycle = createMemoryLifecycle();

  // ── Register tool + commands + auto-capture ─────────────────────────
  registerMemoryTool(pi, mem0, config, getScopeCtx);
  registerCommands(pi, mem0, config, getScopeCtx);
  setupAutoCapture(pi, mem0, config, getScopeCtx, lifecycle);

  // ── session_start: detect project + session, reconstruct scope ──────
  pi.on("session_start", async (_event, ctx) => {
    lifecycle.beginSession();
    scopeCtx.appId = detectAppId(ctx.cwd);

    const sessionFile = ctx.sessionManager?.getSessionFile?.();
    scopeCtx.runId = detectRunId(sessionFile);

    if (config.userId) {
      scopeCtx.userId = config.userId;
    }
  });

  // ── before_agent_start: append memory policy and recall ─────────────
  pi.on("before_agent_start", async (event, _ctx) => {
    let extra = MEMORY_POLICY;

    // Guaranteed retrieval: prefetch memories relevant to this prompt so the
    // agent always has them, rather than depending on it to call the tool.
    const recall = await lifecycle.recall(
      event.prompt ?? "",
      config.contextInjection,
      (q) => mem0.search(q, { filters: resolveSearchFilters("project", scopeCtx) }),
    );
    if (recall) extra += "\n\n" + recall;

    return {
      systemPrompt: (event.systemPrompt ?? "") + "\n\n" + extra,
    };
  });
}
