// idle-summary — Claude-style idle summary + footer one-liner for pi
//
// After configurable idle (default 15m) while `isIdle()`, shows a structured
// session summary in an overlay (Enter/Esc to dismiss). Uses free model
// opencode/big-pickle with openrouter/free fallback — not bound by cost,
// but still tail-budgeted so a 200k-token session doesn't serialize fully.
// Also keeps a one-sentence summary of the last 3 user prompts in the footer
// via setStatus (and optional inline pwd fusion).
//
// Config resolution (lowest wins): defaults < env → settings.json#idleSummary
// → ~/.pi/agent/idle-summary.json → .pi/idle-summary.json → PI_* env.
// Hot-reload with /reload or restart pi.
//
// ponytail: budgeted tail walk is O(n) over session entries <- maxInputTokens cap -> streaming read + early cutoff when session entries exceed 10k
//

// @ts-nocheck — pi extension runs via jiti, not this project's tsconfig; suppress missing pi/node types
// @ts-expect-error — pi types are provided at runtime via jiti; not in this project's tsconfig
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

// ---- deferred imports for theme/tui ----
const _themeCache: any = null;

// ---------------------------------------------------------------------------
// constants
// ---------------------------------------------------------------------------

const FOOTER_KEY = "idle-summary-footer";
const STATUS_KEY = "idle-summary-status";
const DEFAULT_IDLE_MS = 15 * 60 * 1000; // 15 minutes
const DEFAULT_MAX_INPUT_TOKENS = 8000; // relaxed since free model
const TOOL_RESULT_MAX_CHARS = 4000;
const IDLE_DEBOUNCE_MS = 2000;
const FOOTER_DEBOUNCE_MS = 400;

const PRIMARY_MODEL = {
	provider: "opencode",
	id: "muse-spark-1.2-contributor-free",
} as const;
// Fallback chain per user: muse-spark free (cleanest per tests) -> nemotron-3-ultra-free -> openrouter/free
// Tested opencode free on this session: hy3-free (thinking dump/empty), deepseek (unavailable), laguna (short), mimo (429), nemotron-ultra (OK), nemotron-3.5 (OK), x-preview (OK), muse-spark (OK clean) -> selected muse-spark as primary
const FALLBACK_MODELS: Array<{ provider: string; id: string }> = [
	{ provider: "opencode", id: "nemotron-3-ultra-free" },
	{ provider: "openrouter", id: "openrouter/free" },
];

// ---------------------------------------------------------------------------
// state per session (factory is called once per session)
// ---------------------------------------------------------------------------

let idleTimer: ReturnType<typeof setTimeout> | null = null;
let footerTimer: ReturnType<typeof setTimeout> | null = null;
let footerLLMTimer: ReturnType<typeof setTimeout> | null = null;
let isGeneratingIdle = false;
let lastIdleAt = 0;
let lastFooterSentence = "";
let currentCtx: any = null;
let lastBranchChangeUnsub: (() => void) | null = null;

// recent prompts deque
const recentPrompts: string[] = [];

// ---------------------------------------------------------------------------
// tiny helpers
// ---------------------------------------------------------------------------

function tryReadJson(path: string): any | null {
	try {
		const fs = require("node:fs") as typeof import("node:fs");
		if (!fs.existsSync(path)) return null;
		const raw = fs.readFileSync(path, "utf8");
		return JSON.parse(raw);
	} catch {
		return null;
	}
}

function getHome(): string {
	return (
		process.env.HOME || process.env.USERPROFILE || require("node:os").homedir()
	);
}

function globalConfigPath(): string {
	const path = require("node:path") as typeof import("node:path");
	return path.join(getHome(), ".pi", "agent", "idle-summary.json");
}

function settingsJsonPath(): string {
	const path = require("node:path") as typeof import("node:path");
	return path.join(getHome(), ".pi", "agent", "settings.json");
}

function projectConfigPath(cwd: string): string {
	const path = require("node:path") as typeof import("node:path");
	return path.join(cwd, ".pi", "idle-summary.json");
}

type IdleConfig = {
	enabled?: boolean;
	timeoutMs?: number;
	timeoutMinutes?: number;
	maxInputTokens?: number;
	footerLLM?: boolean;
	footerInline?: boolean;
};

function loadIdleConfig(cwd?: string): IdleConfig {
	const cfg: IdleConfig = {};

	// 1) settings.json#idleSummary
	const settings = tryReadJson(settingsJsonPath());
	if (
		settings &&
		typeof settings.idleSummary === "object" &&
		settings.idleSummary
	) {
		Object.assign(cfg, settings.idleSummary);
	}

	// 2) global idle-summary.json
	const g = tryReadJson(globalConfigPath());
	if (g && typeof g === "object") Object.assign(cfg, g);

	// 3) project .pi/idle-summary.json
	try {
		const c = cwd || (currentCtx?.sessionManager?.getCwd?.() ?? process.cwd());
		const p = tryReadJson(projectConfigPath(c));
		if (p && typeof p === "object") Object.assign(cfg, p);
	} catch (_e) {
		void _e;
	}

	// 4) env overrides - applied later in getIdleTimeoutMs etc., but also merge here for maxInputTokens
	if (process.env.PI_IDLE_SUMMARY_MAX_INPUT_TOKENS) {
		const v = parseInt(process.env.PI_IDLE_SUMMARY_MAX_INPUT_TOKENS, 10);
		if (!isNaN(v) && v > 0) cfg.maxInputTokens = v;
	}
	if (process.env.PI_IDLE_SUMMARY_FOOTER_LLM) {
		cfg.footerLLM =
			process.env.PI_IDLE_SUMMARY_FOOTER_LLM === "1" ||
			process.env.PI_IDLE_SUMMARY_FOOTER_LLM === "true";
	}
	return cfg;
}

function saveGlobalConfig(patch: IdleConfig): void {
	try {
		const fs = require("node:fs") as typeof import("node:fs");
		const path = require("node:path") as typeof import("node:path");
		const p = globalConfigPath();
		const dir = path.dirname(p);
		if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
		const existing = tryReadJson(p) || {};
		const next = { ...existing, ...patch };
		// strip undefined
		for (const k of Object.keys(next))
			if ((next as any)[k] === undefined) delete (next as any)[k];
		fs.writeFileSync(p, JSON.stringify(next, null, 2) + "\n", "utf8");
	} catch (_e) {
		void _e;
	}
}

function getIdleTimeoutMs(ctx?: any): number {
	// env highest priority
	if (process.env.PI_IDLE_SUMMARY_TIMEOUT_MS) {
		const v = parseInt(process.env.PI_IDLE_SUMMARY_TIMEOUT_MS, 10);
		if (!isNaN(v) && v >= 0) return v;
	}
	if (process.env.PI_IDLE_SUMMARY_MINUTES) {
		const v = parseFloat(process.env.PI_IDLE_SUMMARY_MINUTES);
		if (!isNaN(v) && v >= 0) return Math.round(v * 60 * 1000);
	}
	if (
		process.env.PI_IDLE_SUMMARY_DISABLED === "1" ||
		process.env.PI_IDLE_SUMMARY_DISABLED === "true"
	)
		return 0;

	const cfg = loadIdleConfig(
		ctx?.sessionManager?.getCwd?.() ?? currentCtx?.sessionManager?.getCwd?.(),
	);
	if (typeof cfg.timeoutMs === "number" && cfg.timeoutMs >= 0)
		return cfg.timeoutMs;
	if (typeof cfg.timeoutMinutes === "number" && cfg.timeoutMinutes >= 0)
		return Math.round(cfg.timeoutMinutes * 60 * 1000);
	return DEFAULT_IDLE_MS;
}

function isIdleEnabled(ctx?: any): boolean {
	if (
		process.env.PI_IDLE_SUMMARY_DISABLED === "1" ||
		process.env.PI_IDLE_SUMMARY_DISABLED === "true"
	)
		return false;
	const cfg = loadIdleConfig(
		ctx?.sessionManager?.getCwd?.() ?? currentCtx?.sessionManager?.getCwd?.(),
	);
	if (cfg.enabled === false) return false;
	return true;
}

function getMaxInputTokens(ctx?: any): number {
	const cfg = loadIdleConfig(
		ctx?.sessionManager?.getCwd?.() ?? currentCtx?.sessionManager?.getCwd?.(),
	);
	if (typeof cfg.maxInputTokens === "number" && cfg.maxInputTokens > 0)
		return cfg.maxInputTokens;
	return DEFAULT_MAX_INPUT_TOKENS;
}

function shouldUseFooterLLM(ctx?: any): boolean {
	const cfg = loadIdleConfig(
		ctx?.sessionManager?.getCwd?.() ?? currentCtx?.sessionManager?.getCwd?.(),
	);
	if (cfg.footerLLM === false) return false;
	if (cfg.footerLLM === true) return true;
	// default: yes, since free model
	return true;
}

function shouldUseFooterInline(ctx?: any): boolean {
	const cfg = loadIdleConfig(
		ctx?.sessionManager?.getCwd?.() ?? currentCtx?.sessionManager?.getCwd?.(),
	);
	// default on — show sentence inline next to pwd
	if (cfg.footerInline === false) return false;
	return true;
}

function safeIsIdle(ctx: any): boolean {
	try {
		return !!ctx?.isIdle?.();
	} catch (_e) {
		void _e;
		return false;
	}
}

function pickSummaryModel(ctx: any): any | null {
	// primary — return even without auth for free/big-pickle (let complete fail visibly)
	try {
		const m = ctx.modelRegistry.find(PRIMARY_MODEL.provider, PRIMARY_MODEL.id);
		if (m) {
			try {
				if (ctx.modelRegistry.hasConfiguredAuth(m)) return m;
			} catch (_e) {
				void _e;
			}
			// free model: return even without strict auth
			if (/free|big-pickle/i.test(m.id)) return m;
		}
	} catch (_e) {
		void _e;
	}
	// fallbacks — same relaxed check
	for (const f of FALLBACK_MODELS) {
		try {
			const m = ctx.modelRegistry.find(f.provider, f.id);
			if (m) {
				try {
					if (ctx.modelRegistry.hasConfiguredAuth(m)) return m;
				} catch (_e) {
					void _e;
				}
				if (/free|big-pickle/i.test(m.id)) return m;
			}
		} catch (_e) {
			void _e;
		}
	}
	// any free auth'd model whose id contains free
	try {
		const avail: any[] = ctx.modelRegistry.getAvailable?.() ?? [];
		const free = avail.filter((a: any) => {
			try {
				return ctx.modelRegistry.hasConfiguredAuth(a) && /free/i.test(a.id);
			} catch (_e) {
				void _e;
				return /free/i.test(a.id);
			}
		});
		if (free.length) return free[0];
		const anyAuth = avail.filter((a: any) => {
			try {
				return ctx.modelRegistry.hasConfiguredAuth(a);
			} catch (_e) {
				void _e;
				return false;
			}
		});
		if (anyAuth.length) return anyAuth[0];
		// last resort: any model at all (free even without auth)
		const anyFree = avail.filter((a: any) => /free|big-pickle/i.test(a.id));
		if (anyFree.length) return anyFree[0];
		if (avail.length) return avail[0];
	} catch (_e) {
		void _e;
	}
	// last: current session model
	if (ctx.model) return ctx.model;
	return null;
}

function getDynamicOpencodeFree(ctx: any): any | null {
	// Find opencode model with "free" in id that is not hy3-free nor deepseek-v4-flash-free, via api (getAvailable)
	try {
		const avail: any[] = ctx.modelRegistry.getAvailable?.() ?? [];
		const candidates = avail.filter(
			(a: any) =>
				a.provider === "opencode" &&
				/free/i.test(a.id) &&
				a.id !== "muse-spark-1.2-contributor-free" &&
				a.id !== "nemotron-3-ultra-free",
		);
		if (candidates.length) {
			// prefer auth'd, else any
			for (const c of candidates) {
				try {
					if (ctx.modelRegistry.hasConfiguredAuth(c)) return c;
				} catch (_e) {
					void _e;
				}
			}
			return candidates[0];
		}
	} catch (_e) {
		void _e;
	}
	return null;
}

function isRateLimitedError(e: any): boolean {
	const msg = String(e?.message ?? e ?? "").toLowerCase();
	return (
		msg.includes("429") ||
		msg.includes("rate limit") ||
		msg.includes("rate_limited") ||
		msg.includes("quota")
	);
}

async function tryCompleteWithFallback(
	ctx: any,
	messages: any,
	maxTokens: number,
	primary: any,
): Promise<any> {
	const chain: any[] = [];
	if (primary) chain.push(primary);
	// nemotron fallback (second per tests, clean)
	try {
		const ds = ctx.modelRegistry.find("opencode", "nemotron-3-ultra-free");
		if (ds && ds.id !== primary?.id) chain.push(ds);
	} catch (_e) {
		void _e;
	}
	// dynamic opencode free (any other free)
	const dyn = getDynamicOpencodeFree(ctx);
	if (dyn && !chain.some((c) => c.provider === dyn.provider && c.id === dyn.id))
		chain.push(dyn);
	// openrouter/free (third per user spec)
	try {
		const ro = ctx.modelRegistry.find("openrouter", "openrouter/free");
		if (ro && !chain.some((c) => c.provider === ro.provider && c.id === ro.id))
			chain.push(ro);
	} catch (_e) {
		void _e;
	}
	// fallback to any free as last resort
	if (chain.length === 0) {
		const alt = pickSummaryModel(ctx);
		if (alt) chain.push(alt);
	}
	let lastErr: any = null;
	for (let i = 0; i < chain.length; i++) {
		const m = chain[i];
		try {
			// for free models, allow even without strict auth
			const isFree = /free|big-pickle/i.test(m.id);
			if (!isFree) {
				try {
					if (!ctx.modelRegistry.hasConfiguredAuth(m)) continue;
				} catch (_e) {
					void _e;
					continue;
				}
			}
			const resp = await ctx.modelRegistry.complete(m, { messages }, {
				maxTokens,
				cacheRetention: "none",
				sessionId: getUuid(),
			} as any);
			if (i > 0) {
				try {
					const fs = require("node:fs") as typeof import("node:fs");
					fs.appendFileSync(
						"/tmp/idle-summary-debug.log",
						`[fallback-success] ${new Date().toISOString()} used ${m.provider}/${m.id} after ${i} fallbacks\n`,
					);
				} catch (_e) {
					void _e;
				}
			}
			return resp;
		} catch (e) {
			lastErr = e;
			try {
				const fs = require("node:fs") as typeof import("node:fs");
				fs.appendFileSync(
					"/tmp/idle-summary-debug.log",
					`[fallback-attempt] ${new Date().toISOString()} ${m.provider}/${m.id} failed: ${String((e as any)?.message ?? e).slice(0, 200)}\n`,
				);
			} catch (_e) {
				void _e;
			}
		}
	}
	throw lastErr ?? new Error("no model in fallback chain");
}

function estimateEntryTokens(entry: any): number {
	try {
		if (entry.type === "message" && entry.message) {
			const msg = entry.message;
			let chars = 0;
			const content = msg.content;
			if (typeof content === "string") chars += content.length;
			else if (Array.isArray(content)) {
				for (const b of content) {
					if (!b || typeof b !== "object") continue;
					if (b.type === "text" && typeof b.text === "string")
						chars += b.text.length;
					else if (b.type === "thinking" && typeof b.thinking === "string")
						chars += b.thinking.length;
					else if (b.type === "toolCall")
						chars += (b.name?.length ?? 0) + JSON.stringify(b.arguments ?? {}).length;
					else if (typeof b.text === "string") chars += b.text.length;
				}
			}
			if (msg.role === "toolResult" && typeof content === "string")
				chars = content.length;
			return Math.ceil(chars / 4) || 20;
		}
		if (entry.type === "compaction" && typeof entry.summary === "string")
			return Math.ceil(entry.summary.length / 4);
		if (entry.type === "branch_summary" && typeof entry.summary === "string")
			return Math.ceil(entry.summary.length / 4);
	} catch (_e) {
		void _e;
	}
	return 50;
}

function contentTextBlocks(content: unknown): string {
	if (typeof content === "string") return content;
	if (!Array.isArray(content)) return "";
	let out = "";
	for (const b of content as any[]) {
		if (!b || typeof b !== "object") continue;
		if (b.type === "text" && typeof b.text === "string") out += b.text + "\n";
	}
	return out.trim();
}

function truncateForSummary(text: string, max: number): string {
	if (text.length <= max) return text;
	return text.slice(0, max - 20) + "\n... [truncated]";
}

function buildConversationTextFromEntries(entries: any[]): string {
	const parts: string[] = [];
	for (const entry of entries) {
		if (entry.type !== "message" || !entry.message?.role) {
			if (entry.type === "compaction" && entry.summary) {
				parts.push(`[Compaction summary]: ${entry.summary}`);
			} else if (entry.type === "branch_summary" && entry.summary) {
				parts.push(`[Branch summary]: ${entry.summary}`);
			}
			continue;
		}
		const role = entry.message.role;
		if (role === "user") {
			const t = contentTextBlocks(entry.message.content);
			if (t) parts.push(`[User]: ${t}`);
		} else if (role === "assistant") {
			const text = contentTextBlocks(entry.message.content);
			if (text) parts.push(`[Assistant]: ${text}`);
			// tool calls
			if (Array.isArray(entry.message.content)) {
				const calls: string[] = [];
				for (const b of entry.message.content as any[]) {
					if (b.type === "toolCall" && typeof b.name === "string") {
						const args = b.arguments ?? {};
						const argStr = Object.entries(args)
							.map(([k, v]) => `${k}=${JSON.stringify(v)}`)
							.slice(0, 3)
							.join(", ");
						calls.push(`${b.name}(${argStr})`);
					}
				}
				if (calls.length) parts.push(`[Assistant tool calls]: ${calls.join("; ")}`);
			}
		} else if (role === "toolResult") {
			const t = contentTextBlocks(entry.message.content);
			if (t)
				parts.push(
					`[Tool result ${entry.message.toolName ?? ""}]: ${truncateForSummary(t, TOOL_RESULT_MAX_CHARS)}`,
				);
		} else if (role === "custom") {
			const t = contentTextBlocks(entry.message.content);
			if (t)
				parts.push(
					`[Custom ${entry.message.customType ?? ""}]: ${truncateForSummary(t, 800)}`,
				);
		}
	}
	return parts.join("\n\n");
}

function buildBudgetedConversation(ctx: any, maxInputTokens: number): string {
	let entries: any[] = [];
	try {
		if (ctx.sessionManager.buildContextEntries)
			entries = ctx.sessionManager.buildContextEntries();
		else if (ctx.sessionManager.getBranch)
			entries = ctx.sessionManager.getBranch();
		else entries = ctx.sessionManager.getEntries();
	} catch {
		try {
			entries = ctx.sessionManager.getEntries();
		} catch {
			entries = [];
		}
	}
	if (!entries.length) return "";

	// Keep at least 10 recent, at most budget. Compaction entries are cheap but valuable — keep them.
	const budget = maxInputTokens;
	let used = 0;
	const kept: any[] = [];

	// Walk newest → oldest, prefer tail
	for (let i = entries.length - 1; i >= 0; i--) {
		const e = entries[i];
		// Always keep compaction/branch_summary if they exist and we haven't exceeded 1.5x budget
		const isSummary = e.type === "compaction" || e.type === "branch_summary";
		const tokens = estimateEntryTokens(e);
		if (!isSummary && used + tokens > budget && kept.length >= 12) break;
		if (isSummary && used + tokens > budget * 1.5 && kept.length >= 12) break;
		kept.push(e);
		used += tokens;
		if (used >= budget * 1.5) break;
	}
	kept.reverse();

	let text = buildConversationTextFromEntries(kept);
	const maxChars = budget * 4;
	if (text.length > maxChars) {
		// keep tail heavier (recent) + head (summary)
		const head = text.slice(0, Math.floor(maxChars * 0.25));
		const tail = text.slice(-Math.floor(maxChars * 0.75));
		text = head + "\n\n... [middle truncated for budget] ...\n\n" + tail;
	}
	return text;
}

function buildSummaryPrompt(conversationText: string, ctx: any): string {
	let cwd = "";
	try {
		cwd = ctx.sessionManager.getCwd?.() ?? process.cwd();
	} catch {
		cwd = process.cwd();
	}
	return [
		"Summarize this pi coding session so the user can resume after being away.",
		"Include: goals & intent, key decisions, progress & files changed, current state, blockers/open questions, and concrete next steps.",
		"Keep it concise and structured with headings. No preamble, no hedging.",
		"",
		`cwd: ${cwd}`,
		"",
		"<conversation>",
		conversationText,
		"</conversation>",
	].join("\n");
}

function getUuid(): string {
	try {
		const ai = require("@earendil-works/pi-ai") as any;
		if (ai.uuidv7) return ai.uuidv7();
	} catch (_e) {
		void _e;
	}
	try {
		return require("node:crypto").randomUUID();
	} catch {
		return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
	}
}

// ---------------------------------------------------------------------------
// footer one-liner
// ---------------------------------------------------------------------------

function pushRecentPrompt(raw: string): void {
	const firstLine = String(raw ?? "")
		.split("\n")[0]
		.trim()
		.slice(0, 220);
	if (!firstLine) return;
	// de-dupe consecutive identical
	if (
		recentPrompts.length &&
		recentPrompts[recentPrompts.length - 1] === firstLine
	)
		return;
	recentPrompts.push(firstLine);
	if (recentPrompts.length > 3) recentPrompts.shift();
}

function localFooterSentence(): string {
	if (!recentPrompts.length) return "";
	const raw =
		recentPrompts.length === 1 ? recentPrompts[0] : recentPrompts.join(" › ");
	// enforce 8 words max
	const words = raw.trim().split(/\s+/).slice(0, 8).join(" ");
	return words.slice(0, 60);
}

function scheduleFooterUpdate(ctx: any): void {
	currentCtx = ctx;
	if (footerTimer) clearTimeout(footerTimer);
	footerTimer = setTimeout(() => void doFooterUpdate(ctx), FOOTER_DEBOUNCE_MS);
	(footerTimer as any).unref?.();
}

async function doFooterUpdate(ctx: any): Promise<void> {
	if (!ctx.hasUI) return;
	currentCtx = ctx;
	if (!recentPrompts.length) {
		try {
			ctx.ui.setStatus(FOOTER_KEY, undefined);
		} catch (_e) {
			void _e;
		}
		if (shouldUseFooterInline(ctx) && lastFooterSentence) {
			// clear inline too by restoring footer
			try {
				ctx.ui.setFooter(undefined);
			} catch (_e) {
				void _e;
			}
			lastFooterSentence = "";
		}
		return;
	}

	let sentence = localFooterSentence();

	if (shouldUseFooterLLM(ctx)) {
		// debounce LLM a bit more
		if (footerLLMTimer) clearTimeout(footerLLMTimer);
		// try LLM, but show local immediately then upgrade
		const localImmediate = sentence;
		showFooterSentence(ctx, localImmediate);
		lastFooterSentence = localImmediate;

		await new Promise<void>((resolve) => {
			footerLLMTimer = setTimeout(async () => {
				try {
					// Prefer openrouter/free for one-liner (hy3-free often returns empty content with reasoning only)
					let model = null;
					try {
						model = ctx.modelRegistry.find("openrouter", "openrouter/free");
						if (model && !ctx.modelRegistry.hasConfiguredAuth(model)) model = null;
					} catch (_e) {
						void _e;
					}
					if (!model) model = pickSummaryModel(ctx);
					if (!model) return resolve();
					const prompt = [
						"Summarize these last user prompts into ONE concise sentence (max 8 words), capturing current intent.",
						"No preamble, no quotes, just the sentence.",
						"",
						...recentPrompts.map((p, i) => `${i + 1}. ${p}`),
					].join("\n");
					const resp: any = await tryCompleteWithFallback(
						ctx,
						[
							{
								role: "user",
								content: [{ type: "text", text: prompt }],
								timestamp: Date.now(),
							},
						],
						300,
						model,
					);
					let txt = (resp.content ?? [])
						.filter((c: any) => c.type === "text")
						.map((c: any) => String(c.text))
						.join(" ")
						.trim()
						.replace(/\s+/g, " ");
					// hy3-free sometimes returns empty content with reasoning in separate field; try to extract
					if ((!txt || txt.length < 4) && resp) {
						try {
							const alt =
								(resp as any).content?.find?.((c: any) => c.type === "thinking")
									?.thinking ??
								(resp as any).reasoning ??
								"";
							if (alt && typeof alt === "string") {
								// reasoning often contains the sentence in last line
								const lines = alt
									.split("\n")
									.map((s: string) => s.trim())
									.filter(Boolean);
								const candidate = lines[lines.length - 1] ?? alt;
								// strip quotes
								const cleaned = candidate.replace(/^["']|["']$/g, "").trim();
								if (cleaned.length > 10 && cleaned.length < 200) txt = cleaned;
							}
						} catch (_e) {
							void _e;
						}
					}
					if (txt && txt.length > 3) {
						// enforce 8 words max
						const w = txt.trim().split(/\s+/).slice(0, 8).join(" ");
						sentence = w.slice(0, 80);
						lastFooterSentence = sentence;
						showFooterSentence(ctx, sentence);
					} else {
						// leave verbatim but log
						try {
							const fs = require("node:fs") as typeof import("node:fs");
							fs.appendFileSync(
								"/tmp/idle-summary-debug.log",
								`[footer-empty] ${new Date().toISOString()} model=${model.provider}/${model.id} content empty\n`,
							);
						} catch (_e) {
							void _e;
						}
					}
				} catch (_e) {
					void _e;
				}
				resolve();
			}, 600);
			(footerLLMTimer as any).unref?.();
			// also resolve after 2s even if timer pending, so local shows
			setTimeout(resolve, 50);
		});
		return;
	}

	lastFooterSentence = sentence;
	showFooterSentence(ctx, sentence);
}

function showFooterSentence(ctx: any, sentence: string): void {
	if (!sentence) {
		try {
			ctx.ui.setStatus(FOOTER_KEY, undefined);
		} catch (_e) {
			void _e;
		}
		return;
	}
	let theme: any = null;
	try {
		theme = ctx.ui.theme;
	} catch (_e) {
		void _e;
	}
	const dot = theme ? theme.fg("dim", "◷ ") : "◷ ";
	const text = theme ? theme.fg("accent", sentence) : sentence;
	// sanitize
	const sanitized = String(dot + text)
		.replace(/[\r\n\t]/g, " ")
		.slice(0, 500);

	try {
		ctx.ui.setStatus(FOOTER_KEY, sanitized);
	} catch (_e) {
		void _e;
	}

	// Optional inline fusion: append sentence to pwd line via custom footer
	// Only if enabled — we replace footer entirely with a clone that adds • sentence
	try {
		if (shouldUseFooterInline(ctx)) {
			installInlineFooter(ctx, sentence);
		} else if (lastBranchChangeUnsub) {
			// restore if we previously installed inline but now disabled
			try {
				ctx.ui.setFooter(undefined);
			} catch (_e) {
				void _e;
			}
			if (lastBranchChangeUnsub) {
				lastBranchChangeUnsub();
				lastBranchChangeUnsub = null;
			}
		}
	} catch (_e) {
		void _e;
	}
}

let inlineSentenceForFooter = "";

// pi's own FooterComponent, resolved once. Wrapping it (instead of cloning its
// render logic) keeps the inline footer in exact parity with the stock one.
let StockFooterComponent: any = null;
function getStockFooterComponent(): any {
	if (StockFooterComponent !== null) return StockFooterComponent;
	try {
		StockFooterComponent = require("@earendil-works/pi-coding-agent").FooterComponent ?? null;
	} catch (_e) {
		void _e;
		StockFooterComponent = null;
	}
	return StockFooterComponent;
}

function installInlineFooter(ctx: any, sentence: string): void {
	inlineSentenceForFooter = sentence;
	const StockFooter = getStockFooterComponent();
	if (!StockFooter) return; // stock footer stays; sentence shows via setStatus line
	try {
		ctx.ui.setFooter((tui: any, theme: any, footerData: any) => {
			const unsub = footerData.onBranchChange(() => tui.requestRender());
			lastBranchChangeUnsub = unsub;
			// Hide our own status inside the wrapped render — the sentence is inline now
			const wrappedFooterData = Object.create(footerData);
			wrappedFooterData.getExtensionStatuses = () => {
				const m = new Map(footerData.getExtensionStatuses());
				m.delete(FOOTER_KEY);
				return m;
			};
			// Shim the session surface FooterComponent reads; getters stay live so
			// model/context changes are reflected on every render.
			const shimSession = {
				get state() {
					return { model: ctx.model, thinkingLevel: ctx.thinkingLevel };
				},
				get sessionManager() {
					return ctx.sessionManager;
				},
				getContextUsage() {
					return ctx.getContextUsage?.();
				},
				// OAuth subscription state isn't reachable from extension contexts;
				// FooterComponent itself still special-cases kimi-coding.
				modelRuntime: { isUsingSubscription: () => false },
			};
			const stockFooter = new StockFooter(shimSession, wrappedFooterData);
			return {
				dispose: () => {
					try {
						unsub();
					} catch (_e) {
						void _e;
					}
					if (lastBranchChangeUnsub === unsub) lastBranchChangeUnsub = null;
				},
				invalidate() {},
				render(width: number): string[] {
					const lines: string[] = stockFooter.render(width);
					// inject idle-summary sentence into the pwd line
					if (inlineSentenceForFooter && lines.length > 0) {
						let truncateToWidth: any = null;
						try {
							truncateToWidth = require("@earendil-works/pi-tui").truncateToWidth;
						} catch (_e) {
							void _e;
						}
						let pwdLine =
							lines[0] + theme.fg("dim", ` • ${inlineSentenceForFooter}`);
						if (truncateToWidth) {
							pwdLine = truncateToWidth(pwdLine, width, theme.fg("dim", "..."));
						}
						lines[0] = pwdLine;
					}
					return lines;
				},
			};
		});
	} catch (_e) {
		void _e;
	}
}

// ---------------------------------------------------------------------------
// idle timer
// ---------------------------------------------------------------------------

function clearIdleTimer(): void {
	if (idleTimer) {
		clearTimeout(idleTimer);
		idleTimer = null;
	}
}

function scheduleIdleTimer(ctx: any): void {
	clearIdleTimer();
	if (!isIdleEnabled(ctx)) return;
	if (!safeIsIdle(ctx)) return;
	const ms = getIdleTimeoutMs(ctx);
	if (!ms || ms <= 0) return;
	// Don't schedule if no user input yet (allow user-only for testing)
	try {
		const entries: any[] = ctx.sessionManager.getEntries();
		const hasUser = entries.some(
			(e: any) => e.type === "message" && e.message?.role === "user",
		);
		if (!hasUser && !recentPrompts.length) return;
	} catch (_e) {
		void _e;
	}
	idleTimer = setTimeout(() => void onIdleTimeout(ctx), ms);
	(idleTimer as any).unref?.();
}

function resetIdleTimer(ctx: any): void {
	clearIdleTimer();
	if (safeIsIdle(ctx)) scheduleIdleTimer(ctx);
}

async function onIdleTimeout(ctx: any): Promise<void> {
	if (isGeneratingIdle) return;
	if (!safeIsIdle(ctx)) {
		try {
			scheduleIdleTimer(ctx);
		} catch (_e) {
			void _e;
		}
		return;
	}
	if (!ctx.hasUI) return;
	// debounce: don't fire again within 0.9x timeout
	const now = Date.now();
	const ms = getIdleTimeoutMs(ctx);
	if (now - lastIdleAt < ms * 0.9 && lastIdleAt !== 0) return;

	// Must have at least one user prompt (assistant optional — allow testing)
	let hasConv = false;
	try {
		const branch: any[] =
			ctx.sessionManager.getBranch?.() ?? ctx.sessionManager.getEntries();
		const hasUser = branch.some(
			(e: any) => e.type === "message" && e.message?.role === "user",
		);
		hasConv = hasUser || recentPrompts.length > 0;
	} catch (_e) {
		void _e;
	}
	if (!hasConv) {
		scheduleIdleTimer(ctx);
		return;
	}

	lastIdleAt = now;
	isGeneratingIdle = true;
	try {
		try {
			ctx.ui.setStatus(
				STATUS_KEY,
				ctx.ui.theme
					? ctx.ui.theme.fg("dim", "◷ idle summary…")
					: "◷ idle summary…",
			);
		} catch (_e) {
			void _e;
		}
		const summary = await generateIdleSummary(ctx);
		if (!summary || !summary.trim()) {
			try {
				ctx.ui.setStatus(STATUS_KEY, undefined);
			} catch (_e) {
				void _e;
			}
			return;
		}
		try {
			ctx.ui.setStatus(STATUS_KEY, undefined);
		} catch (_e) {
			void _e;
		}
		await showSummaryUi(summary, ctx, { autoIdle: true });
	} catch (e) {
		try {
			ctx.ui.setStatus(STATUS_KEY, undefined);
			ctx.ui.notify(
				`Idle summary failed: ${e instanceof Error ? e.message : String(e)}`,
				"warning",
			);
		} catch (_e) {
			void _e;
		}
	} finally {
		isGeneratingIdle = false;
		try {
			if (safeIsIdle(ctx)) scheduleIdleTimer(ctx);
		} catch (_e) {
			void _e;
		}
	}
}

async function generateIdleSummary(ctx: any): Promise<string> {
	const maxInputTokens = getMaxInputTokens(ctx);
	let conversationText = buildBudgetedConversation(ctx, maxInputTokens);
	if (!conversationText.trim()) {
		// fallback to recent prompts for fresh sessions
		if (recentPrompts.length)
			conversationText = recentPrompts
				.map((p, i) => `[User ${i + 1}]: ${p}`)
				.join("\n");
		else return "";
	}
	const model = pickSummaryModel(ctx);
	if (!model) {
		// local fallback when no LLM available — still show something useful
		return `**Session summary (local, no LLM)**\n\n${conversationText.slice(0, 1200)}`;
	}
	try {
		if (!ctx.modelRegistry.hasConfiguredAuth(model)) {
			// for free models, try anyway; only block if truly no auth and not a free id
			const isFree = /free|big-pickle/i.test(model.id);
			if (!isFree) {
				try {
					ctx.ui.notify(
						`Idle summary: no auth for ${model.provider}/${model.id} — using local fallback`,
						"warning",
					);
				} catch (_e) {
					void _e;
				}
				return `**Session summary (local, no auth for ${model.provider}/${model.id})**\n\n${conversationText.slice(0, 1200)}`;
			}
		}
	} catch (_e) {
		void _e;
	}
	const prompt = buildSummaryPrompt(conversationText, ctx);
	const messages: any = [
		{
			role: "user",
			content: [{ type: "text", text: prompt }],
			timestamp: Date.now(),
		},
	];
	let resp: any;
	try {
		resp = await tryCompleteWithFallback(ctx, messages, 900, model);
	} catch (e) {
		throw e;
	}
	let summary = (resp.content ?? [])
		.filter((c: any) => c.type === "text")
		.map((c: any) => String(c.text))
		.join("\n")
		.trim();
	if (!summary && resp) {
		try {
			const alt =
				(resp as any).content?.find?.((c: any) => c.type === "thinking")
					?.thinking ?? "";
			if (alt) summary = String(alt).trim().slice(0, 2000);
		} catch (_e) {
			void _e;
		}
	}
	return summary;
}

// ---------------------------------------------------------------------------
// overlay UI
// ---------------------------------------------------------------------------

async function showSummaryUi(
	summary: string,
	ctx: any,
	opts?: { autoIdle?: boolean },
): Promise<void> {
	if (!ctx.hasUI) {
		try {
			ctx.ui.notify(summary.slice(0, 400), "info");
		} catch (_e) {
			void _e;
		}
		return;
	}
	// prefer custom overlay; fall back to notify
	if (!ctx.ui.custom) {
		try {
			ctx.ui.notify(summary, "info");
		} catch (_e) {
			void _e;
		}
		return;
	}

	const title = opts?.autoIdle ? "Idle Summary" : "Session Summary";
	const subtitle = opts?.autoIdle
		? `Away for ${Math.round(getIdleTimeoutMs(ctx) / 60000)}m — here's where you left off`
		: "";

	try {
		await ctx.ui.custom(
			(_tui: any, theme: any, _kb: any, done: (v: unknown) => void) => {
				// lazy imports inside closure to avoid top-level cycle
				let Container: any,
					Markdown: any,
					Text: any,
					DynamicBorder: any,
					getMarkdownTheme: any,
					matchesKey: any;
				try {
					const tuiMod = require("@earendil-works/pi-tui") as any;
					Container = tuiMod.Container;
					Markdown = tuiMod.Markdown;
					Text = tuiMod.Text;
					({ matchesKey } = require("@earendil-works/pi-tui") as any);
				} catch (_e) {
					void _e;
				}
				try {
					const piMod = require("@earendil-works/pi-coding-agent") as any;
					DynamicBorder = piMod.DynamicBorder;
					getMarkdownTheme = piMod.getMarkdownTheme;
				} catch (_e) {
					void _e;
				}
				if (!Container || !Text) {
					done(undefined);
					return {
						render: (_w: number) => "",
						handleInput: () => {},
						invalidate: () => {},
					} as any;
				}

				const container = new Container();
				const border = DynamicBorder
					? new DynamicBorder((s: string) => theme.fg("accent", s))
					: null;
				let mdTheme: any = null;
				try {
					mdTheme = getMarkdownTheme ? getMarkdownTheme() : null;
				} catch (_e) {
					void _e;
				}

				if (border) container.addChild(border);
				container.addChild(new Text(theme.fg("accent", theme.bold(title)), 1, 0));
				if (subtitle) container.addChild(new Text(theme.fg("dim", subtitle), 1, 0));
				container.addChild(new Text(theme.fg("dim", "─".repeat(30)), 1, 0));
				if (Markdown && mdTheme)
					container.addChild(new Markdown(summary, 1, 1, mdTheme));
				else container.addChild(new Text(summary, 1, 0));
				container.addChild(
					new Text(theme.fg("dim", "Press Enter or Esc to close"), 1, 0),
				);
				if (border) container.addChild(border);

				return {
					render: (width: number) => container.render(width),
					invalidate: () => container.invalidate(),
					handleInput: (data: string) => {
						const mk = matchesKey ?? ((d: string, k: string) => d.includes(k));
						if (mk(data, "enter") || mk(data, "escape") || mk(data, "q"))
							done(undefined);
					},
				};
			},
		);
	} catch {
		try {
			ctx.ui.notify(summary.slice(0, 800), "info");
		} catch (_e) {
			void _e;
		}
	}
}

// ---------------------------------------------------------------------------
// extension entry
// ---------------------------------------------------------------------------

export default function (pi: ExtensionAPI): void {
	// Capture prompts as early as possible
	pi.on("input", async (event: any, ctx: any) => {
		currentCtx = ctx;
		const raw = String(event.input ?? event.text ?? "");
		if (raw.trim()) pushRecentPrompt(raw);
		scheduleFooterUpdate(ctx);
		resetIdleTimer(ctx);
	});

	pi.on("before_agent_start", async (event: any, ctx: any) => {
		currentCtx = ctx;
		const prompt = String(event.prompt ?? "");
		if (prompt.trim() && !prompt.trim().startsWith("/")) {
			// avoid double-count if input already captured same text
			const last = recentPrompts[recentPrompts.length - 1];
			const firstLine = prompt.split("\n")[0].trim().slice(0, 220);
			if (last !== firstLine) pushRecentPrompt(prompt);
		}
		scheduleFooterUpdate(ctx);
		clearIdleTimer();
		try {
			ctx.ui.setStatus(STATUS_KEY, undefined);
		} catch (_e) {
			void _e;
		}
	});

	pi.on("agent_start", async (_event: any, ctx: any) => {
		currentCtx = ctx;
		clearIdleTimer();
	});

	pi.on("agent_end", async (_event: any, ctx: any) => {
		currentCtx = ctx;
		// agent_end may still be followed by compaction/retry; defer to settled
	});

	pi.on("agent_settled", async (_event: any, ctx: any) => {
		currentCtx = ctx;
		scheduleIdleTimer(ctx);
		scheduleFooterUpdate(ctx);
	});

	pi.on("session_start", async (event: any, ctx: any) => {
		currentCtx = ctx;
		// seed footer from existing branch (last 3 user messages)
		try {
			const entries: any[] = ctx.sessionManager.getEntries();
			recentPrompts.length = 0;
			for (const e of entries) {
				if (e.type === "message" && e.message?.role === "user") {
					const t = contentTextBlocks(e.message.content);
					const first = t.split("\n")[0].trim().slice(0, 220);
					if (first && !first.startsWith("/") && !first.startsWith("<"))
						pushRecentPrompt(first);
				}
			}
			// keep only last 3 after loop (push already caps)
			while (recentPrompts.length > 3) recentPrompts.shift();
			if (recentPrompts.length) scheduleFooterUpdate(ctx);
		} catch (_e) {
			void _e;
		}
		if (event.reason !== "reload") lastIdleAt = 0;
		// delay initial idle schedule slightly so startup doesn't fire immediately
		setTimeout(() => {
			try {
				if (safeIsIdle(currentCtx)) scheduleIdleTimer(currentCtx);
			} catch (_e) {
				void _e;
			}
		}, 2000);
	});

	pi.on("session_shutdown", async (_event: any, _ctx: any) => {
		clearIdleTimer();
		if (footerTimer) {
			clearTimeout(footerTimer);
			footerTimer = null;
		}
		if (footerLLMTimer) {
			clearTimeout(footerLLMTimer);
			footerLLMTimer = null;
		}
		if (lastBranchChangeUnsub) {
			try {
				lastBranchChangeUnsub();
			} catch (_e) {
				void _e;
			}
			lastBranchChangeUnsub = null;
		}
		try {
			currentCtx?.ui?.setStatus?.(FOOTER_KEY, undefined);
		} catch (_e) {
			void _e;
		}
		try {
			currentCtx?.ui?.setStatus?.(STATUS_KEY, undefined);
		} catch (_e) {
			void _e;
		}
		try {
			currentCtx?.ui?.setFooter?.(undefined);
		} catch (_e) {
			void _e;
		}
	});

	// Commands
	pi.registerCommand("idle-summary", {
		description:
			"Idle summary: /idle-summary [now|status|enable|disable|<minutes>|<ms>]",
		handler: async (args: string, ctx: any) => {
			currentCtx = ctx;
			const raw = String(args ?? "").trim();
			const low = raw.toLowerCase();

			if (!low || low === "now" || low === "show") {
				let summary = "";
				let dbgInfo = "";
				try {
					try {
						ctx.ui.setStatus(
							STATUS_KEY,
							ctx.ui.theme
								? ctx.ui.theme.fg("dim", "◷ summarizing…")
								: "◷ summarizing…",
						);
					} catch (_e) {
						void _e;
					}
					summary = await generateIdleSummary(ctx);
				} catch (e) {
					dbgInfo = `generate error: ${String((e as any)?.message ?? e).slice(0, 200)}`;
					try {
						const fs = require("node:fs") as typeof import("node:fs");
						fs.appendFileSync(
							"/tmp/idle-summary-debug.log",
							`[generate-error] ${new Date().toISOString()} ${dbgInfo}\n`,
						);
					} catch (_e) {
						void _e;
					}
				} finally {
					try {
						ctx.ui.setStatus(STATUS_KEY, undefined);
					} catch (_e) {
						void _e;
					}
				}
				if (!summary || !summary.trim()) {
					// ultimate local fallback — never show empty warning if we have any context
					try {
						const fs = require("node:fs") as typeof import("node:fs");
						let conv = "";
						try {
							conv = buildBudgetedConversation(ctx, 2000);
						} catch (_e) {
							void _e;
						}
						const recent = recentPrompts.length
							? recentPrompts.join(" | ")
							: "(no recentPrompts)";
						const entriesLen = (() => {
							try {
								return ctx.sessionManager.getEntries().length;
							} catch (_e) {
								return -1;
							}
						})();
						dbgInfo = `convLen=${conv.length} entries=${entriesLen} recent=${recentPrompts.length} model=${pickSummaryModel(ctx)?.id ?? "none"} ${dbgInfo}`;
						fs.appendFileSync(
							"/tmp/idle-summary-debug.log",
							`[no-summary] ${new Date().toISOString()} ${dbgInfo} recent=[${recent}]\n`,
						);
					} catch (_e) {
						void _e;
					}
					// build emergency summary from whatever we have
					try {
						let conv2 = "";
						try {
							conv2 = buildBudgetedConversation(ctx, 2000);
						} catch (_e) {
							void _e;
						}
						if (!conv2.trim() && recentPrompts.length)
							conv2 = recentPrompts.map((p, i) => `[User ${i + 1}]: ${p}`).join("\n");
						if (conv2.trim())
							summary = `**Session summary (emergency local fallback)**\n\n${conv2.slice(0, 1500)}\n\n_\n${dbgInfo}_`;
					} catch (_e) {
						void _e;
					}
				}
				if (!summary || !summary.trim()) {
					if (ctx.hasUI)
						ctx.ui.notify(
							`No summary generated (${dbgInfo || "no conversation"}) — see /tmp/idle-summary-debug.log`,
							"warning",
						);
					return;
				}
				await showSummaryUi(summary, ctx);
				return;
			}

			if (low === "status") {
				const ms = getIdleTimeoutMs(ctx);
				const cfg = loadIdleConfig(ctx.sessionManager.getCwd?.());
				const enabled = isIdleEnabled(ctx);
				const hasTimer = !!idleTimer;
				const model = pickSummaryModel(ctx);
				const modelStr = model ? `${model.provider}/${model.id}` : "none";
				const msg = [
					`enabled: ${enabled}`,
					`timeout: ${ms}ms (${(ms / 60000).toFixed(2)}m)`,
					`hasTimer: ${hasTimer}`,
					`maxInputTokens: ${getMaxInputTokens(ctx)}`,
					`model: ${modelStr}`,
					`footer: ${recentPrompts.length ? `"${localFooterSentence()}"` : "(none)"} → ${lastFooterSentence ? `"${lastFooterSentence}"` : "(pending)"}`,
					`config: ${JSON.stringify(cfg)}`,
				].join("\n");
				if (ctx.hasUI) ctx.ui.notify(msg, "info");
				else console.log(msg);
				return;
			}

			if (low === "enable" || low === "on") {
				saveGlobalConfig({ enabled: true });
				if (ctx.hasUI) ctx.ui.notify("Idle summary enabled", "info");
				scheduleIdleTimer(ctx);
				return;
			}
			if (low === "disable" || low === "off") {
				saveGlobalConfig({ enabled: false });
				clearIdleTimer();
				if (ctx.hasUI) ctx.ui.notify("Idle summary disabled", "info");
				return;
			}

			// numeric: 15, 15m, 900000, 5.5
			const mMatch = low.match(/^(\d+(?:\.\d+)?)\s*m(in)?$/);
			if (mMatch) {
				const mins = parseFloat(mMatch[1]);
				if (!isNaN(mins) && mins >= 0) {
					saveGlobalConfig({ timeoutMinutes: mins, timeoutMs: undefined as any });
					if (ctx.hasUI) ctx.ui.notify(`Idle timeout set to ${mins}m`, "info");
					scheduleIdleTimer(ctx);
					return;
				}
			}
			const n = parseFloat(low);
			if (!isNaN(n) && isFinite(n)) {
				if (n >= 1000) {
					// treat as ms
					saveGlobalConfig({
						timeoutMs: Math.round(n),
						timeoutMinutes: undefined as any,
					});
					if (ctx.hasUI)
						ctx.ui.notify(`Idle timeout set to ${Math.round(n)}ms`, "info");
				} else {
					saveGlobalConfig({ timeoutMinutes: n, timeoutMs: undefined as any });
					if (ctx.hasUI) ctx.ui.notify(`Idle timeout set to ${n}m`, "info");
				}
				scheduleIdleTimer(ctx);
				return;
			}

			if (low.startsWith("footer")) {
				const parts = low.split(/\s+/);
				if (parts[1] === "inline") {
					const on = parts[2] !== "off" && parts[2] !== "disable";
					saveGlobalConfig({ footerInline: on });
					if (ctx.hasUI)
						ctx.ui.notify(`Footer inline ${on ? "enabled" : "disabled"}`, "info");
					if (!on) {
						try {
							ctx.ui.setFooter(undefined);
						} catch (_e) {
							void _e;
						}
						if (lastBranchChangeUnsub) {
							try {
								lastBranchChangeUnsub();
							} catch (_e) {
								void _e;
							}
							lastBranchChangeUnsub = null;
						}
					}
					scheduleFooterUpdate(ctx);
					return;
				}
				if (parts[1] === "llm") {
					const on = parts[2] !== "off" && parts[2] !== "disable";
					saveGlobalConfig({ footerLLM: on });
					if (ctx.hasUI)
						ctx.ui.notify(`Footer LLM ${on ? "enabled" : "disabled"}`, "info");
					scheduleFooterUpdate(ctx);
					return;
				}
			}

			if (ctx.hasUI)
				ctx.ui.notify(
					"Usage: /idle-summary [now|status|enable|disable|<minutes>m|<ms>ms|footer inline on|off|footer llm on|off]",
					"info",
				);
		},
	});

	// alias for footer debugging
	pi.registerCommand("idle-footer", {
		description: "Footer one-liner debug",
		handler: async (_args: string, ctx: any) => {
			await doFooterUpdate(ctx);
			if (ctx.hasUI)
				ctx.ui.notify(
					`Footer: ${lastFooterSentence || localFooterSentence() || "(none)"}`,
					"info",
				);
		},
	});
}
