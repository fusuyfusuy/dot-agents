// @ts-nocheck — pi extension runs via jiti, not this project's tsconfig
// Gated tools: pi-web-access tools and the subagent tool are OFF by default.
// The agent must get your OK before first use each session, unless your latest
// user message explicitly tells it to use them (e.g. "use subagents", "search the web").
//
// Requirements:
//   - pi-web-access (web_search, source_check, fetch_content, get_search_content)
//   - pi-subagents  (subagent)
//
// NOTE: approvals live for the pi process lifetime (shared across forks in the
// same run). This is intentional to avoid re-nagging; it resets on a fresh `pi`.
import type {
  ExtensionAPI,
  ExtensionContext,
} from "@earendil-works/pi-coding-agent";

const WEB_TOOLS = new Set([
  "web_search",
  "source_check",
  "fetch_content",
  "get_search_content",
]);
const SUBAGENT_TOOLS = new Set(["subagent"]);

const LABELS: Record<string, string> = {
  web: "web access tools (web_search / source_check / fetch_content / get_search_content)",
  subagents: "the subagent tool",
};

const approved = new Set<string>(); // session-scoped approvals ("web" | "subagents")

function categoryFor(tool: string): string | undefined {
  if (WEB_TOOLS.has(tool)) return "web";
  if (SUBAGENT_TOOLS.has(tool)) return "subagents";
  return undefined;
}

function lastUserText(ctx: ExtensionContext): string {
  const entries = ctx.sessionManager.getEntries();
  for (let i = entries.length - 1; i >= 0; i--) {
    const e = entries[i] as any;
    if (e.type === "message" && e.message?.role === "user") {
      const c = e.message.content;
      return typeof c === "string" ? c : JSON.stringify(c ?? "");
    }
  }
  return "";
}

// True only when the user's own latest message explicitly ordered this category.
function explicitlyAsked(text: string, category: string): boolean {
  const t = text.toLowerCase();
  if (category === "web") {
    return (
      /\b(use|run|call|try|go|open|enable|search|browse|look up|lookup)\b/.test(
        t,
      ) && /\b(web|internet|online|browse|online research)\b/.test(t)
    );
  }
  if (category === "subagents") {
    return (
      /\bsubagent/.test(t) &&
      /\b(use|run|spawn|delegate|launch|enable|try|with|review|audit)/.test(t)
    );
  }
  return false;
}

export default function (pi: ExtensionAPI) {
  // Tell the model to ask first, so it rarely trips the gate in the first place.
  pi.on("context", (event: any) => {
    const note =
      "Cost guard: the web-access tools (web_search/source_check/fetch_content/get_search_content) and " +
      "the subagent tool are OFF by default. Before using any of them, ask me for permission — the gate " +
      'will prompt me and I decide. If I explicitly say to use "web"/"search that" or "subagents" in my ' +
      "message, you may proceed without asking.";
    event.messages = [
      { role: "system", content: note },
      ...(event.messages as any[]),
    ];
    return { messages: event.messages };
  });

  pi.on("tool_call", async (event: any, ctx: any) => {
    const category = categoryFor(event.toolName);
    if (!category || approved.has(category)) return;

    // The user ordered it outright in this latest message: allow immediately.
    if (explicitlyAsked(lastUserText(ctx), category)) {
      approved.add(category);
      return;
    }

    // No interactive user to ask (print/json/headless): block by default.
    if (!ctx.hasUI) {
      return {
        block: true,
        reason:
          `${event.toolName} is gated off by default. The user did not authorize it. ` +
          `Do not use web/subagent tools without the user asking you to.`,
      };
    }

    const ok = await ctx.ui.confirm(
      `Enable ${category}?`,
      `The agent wants to use ${LABELS[category]}.\n\nAllow for the rest of this session?`,
    );
    if (ok) {
      approved.add(category);
      return;
    }
    return {
      block: true,
      reason:
        `Blocked: ${category} tools are off by default and you did not get permission. ` +
        `Ask the user to enable ${category === "subagents" ? "subagents" : "web access"} (e.g. "yes, use web") ` +
        `and then retry.`,
    };
  });
}
