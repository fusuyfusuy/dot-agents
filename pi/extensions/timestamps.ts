// timestamps — inline HH:MM:SS local time for user prompts and assistant blocks
//
// Shows a dim timestamp before every user prompt and assistant block.
// Tool calls are intentionally NOT stamped — they render as muted log lines
// via agy-thinking.ts and should read as background logging, not primary
// output. Always on; composes with compact-tools.ts (patches the container
// rather than re-registering tools).
//
// Install: lives in tui-agent-settings/pi/extensions/ (source) and is
// manually synced to ~/.pi/agent/extensions/. Hot-reload with /reload
// or restart pi. No config required.

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Text } from "@earendil-works/pi-tui";

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

function formatTime(ts: number): string {
	try {
		return new Date(ts).toLocaleTimeString(undefined, {
			hour: "2-digit",
			minute: "2-digit",
			second: "2-digit",
			hour12: false,
		});
	} catch (_e0) {
		void _e0;
		const d = new Date(ts);
		const pad = (n: number) => String(n).padStart(2, "0");
		return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
	}
}

function formatDuration(ms: number): string {
	if (ms < 1000) return `${ms}ms`;
	const s = ms / 1000;
	if (s < 60) return `${s.toFixed(1)}s`;
	const m = Math.floor(s / 60);
	const rs = Math.round(s % 60);
	return `${m}m ${rs}s`;
}

// Heuristic to find the installed pi root without hard-coding the nvm path.
// Works in the jiti extension runner where process.argv[1] is the pi cli.
function findPiRoot(): string | null {
	// eslint-disable-next-line @typescript-eslint/no-require-imports
	const fs = require("node:fs") as typeof import("node:fs");
	// eslint-disable-next-line @typescript-eslint/no-require-imports
	const path = require("node:path") as typeof import("node:path");
	const candidates: string[] = [];

	// 1) cli entry (e.g. .../dist/cli.js)
	if (process.argv[1]) {
		try {
			const real = fs.realpathSync(process.argv[1]);
			candidates.push(path.dirname(real));
			candidates.push(path.resolve(path.dirname(real), ".."));
			candidates.push(path.resolve(path.dirname(real), "../.."));
		} catch (_e) {
			void _e;
		}
		candidates.push(path.dirname(process.argv[1]));
	}

	// 2) common global install locations
	try {
		const cp = require("node:child_process") as typeof import("node:child_process");
		const globalRoot = cp.execSync("npm root -g 2>/dev/null || true", { encoding: "utf8" }).trim();
		if (globalRoot) {
			candidates.push(path.join(globalRoot, "@earendil-works", "pi-coding-agent"));
		}
	} catch (_e) {
		void _e;
	}
	try {
		const home = process.env.HOME;
		if (home) {
			// try to discover via `which pi`
			const which = require("node:child_process")
				.execSync("which pi 2>/dev/null || true", { encoding: "utf8" })
				.trim();
			if (which) {
				try {
					const real = fs.realpathSync(which);
					candidates.push(path.dirname(real));
					candidates.push(path.resolve(path.dirname(real), ".."));
				} catch (_e2) {
					void _e2;
				}
			}
		}
	} catch (_e3) {
		void _e3;
	}

	for (const base of candidates) {
		let dir = base;
		for (let i = 0; i < 6; i++) {
			const probe = path.join(
				dir,
				"dist/modes/interactive/components/tool-execution.js",
			);
			if (fs.existsSync(probe)) return dir;
			const parent = path.dirname(dir);
			if (parent === dir) break;
			dir = parent;
		}
	}
	return null;
}

// ---------------------------------------------------------------------------
// extension
// ---------------------------------------------------------------------------

export default function (pi: ExtensionAPI) {
	const pendingUser: Array<{ text: string; ts: number }> = [];

	// Capture user prompt time as early as possible. `input` fires for real
	// keystrokes, `before_agent_start` covers programmatic prompts and
	// skill expansions where input may have been transformed.
	pi.on("input", async (event) => {
		pendingUser.push({
			text: String((event as any).input ?? ""),
			ts: Date.now(),
		});
		if (pendingUser.length > 80) pendingUser.shift();
	});

	pi.on("before_agent_start", async (event) => {
		const prompt = String((event as any).prompt ?? "");
		if (prompt.trim()) {
			pendingUser.push({ text: prompt, ts: Date.now() });
			if (pendingUser.length > 80) pendingUser.shift();
		}
	});

	// Try prototype patching — this composes with compact-tools.ts because we
	// patch the container, not the tool renderer. Runs async so the extension
	// factory itself stays synchronous (pi awaits it only if it returns a Promise,
	// but keeping it sync avoids blocking startup).
	(async () => {
		let theme: any = null;
		try {
			const root = findPiRoot();
			if (root) {
				// Dynamic import via file:// bypasses the package "exports" map.
				const path = require("node:path") as typeof import("node:path");
				const { pathToFileURL } = require("node:url") as typeof import("node:url");
				try {
					const themeUrl = pathToFileURL(
						path.join(root, "dist/modes/interactive/theme/theme.js"),
					).href;
					const themeMod: any = await import(themeUrl);
					theme = themeMod.theme;
				} catch (_e4) {
					void _e4; // theme optional — fall back to raw ANSI dim
				}
			}
		} catch (_e8) {
			void _e8;
		}

		const dim = (s: string) =>
			theme ? theme.fg("dim", s) : `\x1b[2m${s}\x1b[22m`;
		const muted = (s: string) =>
			theme ? theme.fg("muted", s) : `\x1b[2m${s}\x1b[22m`;

		// -------------------------------------------------------------------
		// Patch UserMessageComponent
		// -------------------------------------------------------------------
		try {
			const root = findPiRoot();
			if (root) {
				const path = require("node:path") as typeof import("node:path");
				const { pathToFileURL } = require("node:url") as typeof import("node:url");
				const url = pathToFileURL(
					path.join(root, "dist/modes/interactive/components/user-message.js"),
				).href;
				const mod: any = await import(url);
				const Ctor = mod.UserMessageComponent;
				if (Ctor && !Ctor.prototype.__timestampsPatched) {
					const origRebuild = Ctor.prototype.rebuild;
					Ctor.prototype.rebuild = function (this: any) {
						// Find best matching pending timestamp (exact text or prefix)
						let ts = Date.now();
						const txt: string = String(this.text ?? "");
						const trimmed = txt.trim();
						if (trimmed) {
							for (let i = pendingUser.length - 1; i >= 0; i--) {
								const cand = pendingUser[i].text.trim();
								if (!cand) continue;
								if (
									cand === trimmed ||
									trimmed.startsWith(cand) ||
									cand.startsWith(trimmed) ||
									trimmed.includes(cand.slice(0, 80))
								) {
									ts = pendingUser[i].ts;
									pendingUser.splice(i, 1);
									break;
								}
							}
						}
						// Call original to build contentBox
						origRebuild.call(this);
						// Insert timestamp line as first child (before the boxed content)
						const timeLabel = `${dim(`[${formatTime(ts)}]`)} ${muted("you")}`;
						// UserMessageComponent stores outputPad on `this`
						const pad = typeof this.outputPad === "number" ? this.outputPad : 1;
						const stamp = new Text(timeLabel, pad, 0);
						// Re-order: stamp first, then existing children (contentBox)
						const existing = [...this.children];
						this.clear();
						this.addChild(stamp);
						for (const c of existing) this.addChild(c);
					};
					Ctor.prototype.__timestampsPatched = true;
				}
			}
		} catch (e) {
			// Patch is best-effort; tool wrapping fallback will still cover prompts via status
			// eslint-disable-next-line no-console
			console.error("[timestamps] UserMessage patch failed", e);
		}

		// -------------------------------------------------------------------
		// Patch AssistantMessageComponent
		// -------------------------------------------------------------------
		try {
			const root = findPiRoot();
			if (root) {
				const path = require("node:path") as typeof import("node:path");
				const { pathToFileURL } = require("node:url") as typeof import("node:url");
				const url = pathToFileURL(
					path.join(root, "dist/modes/interactive/components/assistant-message.js"),
				).href;
				const mod: any = await import(url);
				const Ctor = mod.AssistantMessageComponent;
				if (Ctor && !Ctor.prototype.__timestampsPatched) {
					const origUpdate = Ctor.prototype.updateContent;
					Ctor.prototype.updateContent = function (
						this: any,
						message: any,
						isStreaming: any,
					) {
						origUpdate.call(this, message, isStreaming);
						if (isStreaming) return;
						if (!message) return;
						const ts: number | undefined = message.timestamp;
						if (!ts) return;
						// Avoid double-inserting if already stamped (e.g. re-render)
						// Mark via a property on the container
						if (this.__timestampsStampedFor === ts) return;
						this.__timestampsStampedFor = ts;

						// Insert timestamp as first child of contentContainer, before the spacer
						const container = this.contentContainer;
						if (!container || !Array.isArray(container.children)) return;
						const label = `${dim(`[${formatTime(ts)}]`)} ${muted("assistant")}`;
						const pad = typeof this.outputPad === "number" ? this.outputPad : 1;
						const stamp = new Text(label, pad, 0);
						const existing = [...container.children];
						container.clear();
						container.addChild(stamp);
						for (const c of existing) container.addChild(c);
					};
					Ctor.prototype.__timestampsPatched = true;
				}
			}
		} catch (e) {
			// eslint-disable-next-line no-console
			console.error("[timestamps] AssistantMessage patch failed", e);
		}
	})();
}

function installToolWrapping(
	pi: ExtensionAPI,
	toolStart: Map<string, number>,
	dim: (s: string) => string,
	muted: (s: string) => string,
) {
	// Avoid double-install
	if ((pi as any).__timestampsFallbackInstalled) return;
	(pi as any).__timestampsFallbackInstalled = true;

	try {
		// Lazy import — these re-exports live on the pi host and resolve via jiti
		// even though the extension file itself is in ~/.pi/agent/extensions/.
		// We import inside the function so a missing export doesn't break the main path.
		const cwd = process.cwd();
		// eslint-disable-next-line @typescript-eslint/no-require-imports
		const piMod = require("@earendil-works/pi-coding-agent") as any;
		const creators: Array<{ name: string; create: (cwd: string) => any }> = [];
		if (piMod.createBashTool)
			creators.push({ name: "bash", create: piMod.createBashTool });
		if (piMod.createReadTool)
			creators.push({ name: "read", create: piMod.createReadTool });
		if (piMod.createEditTool)
			creators.push({ name: "edit", create: piMod.createEditTool });
		if (piMod.createWriteTool)
			creators.push({ name: "write", create: piMod.createWriteTool });
		if (piMod.createGrepTool)
			creators.push({ name: "grep", create: piMod.createGrepTool });
		if (piMod.createFindTool)
			creators.push({ name: "find", create: piMod.createFindTool });
		if (piMod.createLsTool)
			creators.push({ name: "ls", create: piMod.createLsTool });

		for (const { name, create } of creators) {
			let original: any;
			try {
				original = create(cwd);
			} catch (_e5) {
				void _e5;
				continue;
			}
			const origCall = original.renderCall?.bind(original);
			const origResult = original.renderResult?.bind(original);

			pi.registerTool({
				name: original.name,
				label: original.label,
				description: original.description,
				parameters: original.parameters,
				async execute(
					toolCallId: string,
					params: any,
					signal: any,
					onUpdate: any,
					ctx: any,
				) {
					toolStart.set(toolCallId, Date.now());
					return original.execute(toolCallId, params, signal, onUpdate, ctx);
				},
				renderCall(args: any, theme: any, context: any) {
					const ts = toolStart.get(context.toolCallId) ?? Date.now();
					if (!toolStart.has(context.toolCallId))
						toolStart.set(context.toolCallId, ts);
					const prefix = dim(`[${formatTime(ts)}] `);
					if (origCall) {
						try {
							const inner: any = origCall(args, theme, context);
							// Wrap inner Text/component with timestamp prefix if it's a Text
							// Text is the common case for built-ins; we prepend via a Container
							// eslint-disable-next-line @typescript-eslint/no-require-imports
							const { Container } = require("@earendil-works/pi-tui") as any;
							if (inner && typeof inner.render === "function") {
								const wrap = new Container();
								// eslint-disable-next-line @typescript-eslint/no-require-imports
								const { Text: TuiText } = require("@earendil-works/pi-tui") as any;
								wrap.addChild(new TuiText(prefix + muted(name), 0, 0));
								wrap.addChild(inner);
								return wrap;
							}
						} catch (_e6) {
							void _e6;
						}
					}
					return new Text(prefix + theme.fg("toolTitle", theme.bold(name)), 0, 0);
				},
				renderResult(result: any, opts: any, theme: any, context: any) {
					const ts = toolStart.get(context.toolCallId);
					const dur = ts ? Date.now() - ts : 0;
					const durPart = ts && dur > 500 ? dim(` (+${formatDuration(dur)})`) : "";
					if (origResult) {
						try {
							const inner: any = origResult(result, opts, theme, context);
							if (inner && typeof inner.render === "function") {
								// Append duration hint to the existing result view when collapsed
								// We don't try to mutate inner; the timestamp is already in renderCall.
								// For the done state we add a small suffix via a wrapper if needed.
								if (durPart && !opts.expanded) {
									const { Container } = require("@earendil-works/pi-tui") as any;
									const { Text: TuiText } = require("@earendil-works/pi-tui") as any;
									const wrap = new Container();
									wrap.addChild(inner);
									wrap.addChild(new TuiText(dim(`done ${durPart}`), 0, 0));
									return wrap;
								}
								return inner;
							}
						} catch (_e7) {
							void _e7;
						}
					}
					// Fallback result
					const content = result?.content?.[0];
					const text =
						content?.type === "text" ? String(content.text).slice(0, 200) : "";
					return new Text(
						dim(`[${ts ? formatTime(ts) : formatTime(Date.now())}] `) + text,
						0,
						0,
					);
				},
			});
		}
	} catch (e) {
		// eslint-disable-next-line no-console
		console.error("[timestamps] fallback tool wrapping failed", e);
	}
}
