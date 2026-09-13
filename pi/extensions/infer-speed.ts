// infer-speed: live inference speed in the footer
//
// Shows active tok/s while the LLM streams, then the final tok/s for the
// completed turn. Hooks message_start/_update/_end (assistant only) and
// estimates live tokens as chars/4 until the provider reports real usage.
//
// ponytail: live estimate uses chars/4 heuristic — cheap, no tokenizer.
// Final value uses usage.output when available, so the settled number is exact.
// Upgrade path: plug a real tokenizer if you need exact live counts.
//
// Install: lives in tui-agent-settings/pi/extensions/ (source) and is
// manually synced to ~/.pi/agent/extensions/. Hot-reload with /reload
// or restart pi. No config required.

// @ts-expect-error — pi types are provided at runtime via jiti; not in this project's tsconfig
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const STATUS_KEY = "infer-speed";
const CHARS_PER_TOKEN = 4;
const LIVE_INTERVAL_MS = 200;
const FINAL_HOLD_MS = 8000;

// shared state isolated per pi session (exported fn is called once per session)
let startMs = 0;
let chars = 0;
let active = false;
let timer: ReturnType<typeof setInterval> | null = null;
let holdTimer: ReturnType<typeof setTimeout> | null = null;
let currentCtx: any = null;

function clearTimers(): void {
	if (timer) {
		clearInterval(timer);
		timer = null;
	}
	if (holdTimer) {
		clearTimeout(holdTimer);
		holdTimer = null;
	}
}

function formatLive(tokPerSec: number): string {
	if (!currentCtx) return `${Math.round(tokPerSec)} tok/s`;
	const theme = currentCtx.ui.theme;
	const n = theme.fg("accent", `${Math.round(tokPerSec)}`);
	const u = theme.fg("dim", " tok/s");
	const prefix = theme.fg("accent", "⚡ ");
	return prefix + n + u;
}

function formatFinal(tokPerSec: number, tokens: number, secs: number): string {
	if (!currentCtx)
		return `${Math.round(tokPerSec)} tok/s (${tokens} tok in ${secs.toFixed(1)}s)`;
	const theme = currentCtx.ui.theme;
	const check = theme.fg("success", "✓ ");
	const n = theme.fg("success", `${Math.round(tokPerSec)}`);
	const u = theme.fg("dim", " tok/s");
	const detail = theme.fg("dim", ` · ${tokens} tok in ${secs.toFixed(1)}s`);
	return check + n + u + detail;
}

function tick(): void {
	if (!active || !currentCtx) return;
	const elapsed = (Date.now() - startMs) / 1000;
	if (elapsed <= 0) return;
	const estTokens = chars / CHARS_PER_TOKEN;
	const tps = estTokens / elapsed;
	currentCtx.ui.setStatus(STATUS_KEY, formatLive(tps));
}

function handleMessageStart(event: any, ctx: any): void {
	const role = (event.message as any)?.role;
	if (role !== "assistant") return;
	currentCtx = ctx;
	clearTimers();
	startMs = Date.now();
	chars = 0;
	active = true;
	ctx.ui.setStatus(STATUS_KEY, formatLive(0));
	timer = setInterval(tick, LIVE_INTERVAL_MS);
}

function handleMessageUpdate(event: any, ctx: any): void {
	if (!active) return;
	currentCtx = ctx;
	const ev: any = (event as any).assistantMessageEvent;
	if (ev && typeof ev.delta === "string") {
		chars += ev.delta.length;
	} else if (ev?.type === "text_delta" || ev?.type === "thinking_delta") {
		chars += String(ev.delta ?? "").length;
	}
}

function handleMessageEnd(event: any, ctx: any): void {
	const role = (event.message as any)?.role;
	if (role !== "assistant") return;
	if (!active) return;
	active = false;
	currentCtx = ctx;
	if (timer) {
		clearInterval(timer);
		timer = null;
	}
	const elapsed = (Date.now() - startMs) / 1000;
	const usage: any = (event.message as any)?.usage;
	const finalTokens: number =
		typeof usage?.output === "number" && usage.output > 0
			? usage.output
			: Math.round(chars / CHARS_PER_TOKEN);
	const tps = elapsed > 0 ? finalTokens / elapsed : 0;
	const stopReason: string = (event.message as any)?.stopReason ?? "stop";
	const theme = ctx.ui.theme;
	if (stopReason === "aborted" || stopReason === "error") {
		ctx.ui.setStatus(STATUS_KEY, theme.fg("error", `✕ ${stopReason}`));
	} else {
		ctx.ui.setStatus(STATUS_KEY, formatFinal(tps, finalTokens, elapsed));
	}
	holdTimer = setTimeout(() => {
		if (!currentCtx) return;
		if (active) return;
		const dimTheme = currentCtx.ui.theme;
		const dimText = dimTheme.fg(
			"dim",
			`${Math.round(tps)} tok/s · ${finalTokens} tok`,
		);
		currentCtx.ui.setStatus(STATUS_KEY, dimText);
	}, FINAL_HOLD_MS);
}

function handleTurnEnd(_event: any, ctx: any): void {
	currentCtx = ctx;
	if (!active) return;
	active = false;
	clearTimers();
	const elapsed = (Date.now() - startMs) / 1000;
	const estTokens = Math.round(chars / CHARS_PER_TOKEN);
	if (estTokens > 0 && elapsed > 0) {
		ctx.ui.setStatus(
			STATUS_KEY,
			formatFinal(estTokens / elapsed, estTokens, elapsed),
		);
	}
}

function handleSessionStart(_event: any, ctx: any): void {
	currentCtx = ctx;
}

function handleShutdown(): void {
	clearTimers();
}

export default function (pi: ExtensionAPI): void {
	pi.on("message_start", handleMessageStart);
	pi.on("message_update", handleMessageUpdate);
	pi.on("message_end", handleMessageEnd);
	pi.on("turn_end", handleTurnEnd);
	pi.on("session_start", handleSessionStart);
	pi.on("session_shutdown", handleShutdown);
}
