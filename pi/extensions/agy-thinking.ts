// @ts-nocheck — pi extension runs via jiti, not this project's tsconfig
// agy-thinking: collapses reasoning blocks to agy style
//   ▸ Thought for 3s, 673 tokens
//     Analyzing CSS Style
// Full thinking is one key away (follows tool ctrl+o expand — same global toggle).
// Execution is untouched; only TUI rendering is collapsed.
//
// Install: copy to ~/.pi/agent/extensions/ and /reload-runtime or restart pi.
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const CHARS_PER_TOKEN = 4;

function truncated(s: string, max: number, suffix = "..."): string {
  if (s.length <= max) return s;
  return s.slice(0, max - suffix.length) + suffix;
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  const rs = Math.round(s % 60);
  return `${m}m ${rs}s`;
}

function formatTime(ts: number): string {
  try {
    return new Date(ts).toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  } catch {
    const d = new Date(ts);
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }
}

function extractTitle(thinking: string): string {
  const lines = thinking
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);
  if (!lines.length) return "";
  // Prefer first short line that looks like a title (no period, < 80 chars)
  let candidate = lines[0];
  // Strip markdown heading markers
  candidate = candidate.replace(/^#+\s*/, "").trim();
  // If first line is very long, take first sentence
  if (candidate.length > 80) {
    const sent = candidate.split(/[.?!]\s/)[0];
    if (sent && sent.length > 10) candidate = sent;
  }
  return truncated(candidate, 72);
}

// Short single-line summary of a tool call's primary argument
function summarizeToolArgs(args: any): string {
  try {
    if (!args || typeof args !== "object") {
      return typeof args === "string" ? args.trim() : "";
    }
    const preferredKeys = [
      "command",
      "cmd",
      "file_path",
      "path",
      "pattern",
      "url",
      "query",
      "skill",
      "name",
      "description",
    ];
    let s: string | undefined;
    for (const k of preferredKeys) {
      const v = args[k];
      if (typeof v === "string" && v.trim()) {
        s = v;
        break;
      }
    }
    if (!s) {
      const first = Object.values(args).find(
        (v) => typeof v === "string" && String(v).trim(),
      ) as string | undefined;
      s = first;
    }
    if (!s) {
      const j = JSON.stringify(args);
      s = j && j !== "{}" ? j : "";
    }
    return truncated(String(s).replace(/\s+/g, " ").trim(), 56);
  } catch {
    return "";
  }
}

// First meaningful output line of a tool result (for the collapsed preview line)
function firstOutputLine(result: any): string {
  try {
    for (const b of result?.content ?? []) {
      if (b?.type === "text" && typeof b.text === "string") {
        const line = b.text
          .split("\n")
          .map((l: string) => l.trim())
          .find(Boolean);
        if (line) return truncated(line.replace(/\s+/g, " "), 80);
      }
    }
  } catch {}
  return "";
}

function findPiRoot(): string | null {
  const fs = require("node:fs") as typeof import("node:fs");
  const path = require("node:path") as typeof import("node:path");
  const candidates: string[] = [];
  if (process.argv[1]) {
    try {
      const real = fs.realpathSync(process.argv[1]);
      candidates.push(path.dirname(real));
      candidates.push(path.resolve(path.dirname(real), ".."));
      candidates.push(path.resolve(path.dirname(real), "../.."));
    } catch {}
  }
  try {
    const cp = require("node:child_process") as typeof import("node:child_process");
    const globalRoot = cp.execSync("npm root -g 2>/dev/null || true", { encoding: "utf8" }).trim();
    if (globalRoot) {
      candidates.push(path.join(globalRoot, "@earendil-works", "pi-coding-agent"));
    }
  } catch {}
  try {
    const which = require("node:child_process")
      .execSync("which pi 2>/dev/null || true", { encoding: "utf8" })
      .trim();
    if (which) {
      try {
        const real = fs.realpathSync(which);
        candidates.push(path.dirname(real));
        candidates.push(path.resolve(path.dirname(real), ".."));
      } catch {}
    }
  } catch {}
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

export default function (pi: ExtensionAPI) {
  // ---- thinking timing state ----
  let startMs: number | null = null;
  let chars = 0;
  let thoughtsExpanded = false; // mirrors toolOutputExpanded
  const metaByTs = new Map<
    number,
    { durationMs: number; tokens: number; title: string }
  >();

  function handleStart(event: any) {
    const role = (event.message as any)?.role;
    if (role !== "assistant") return;
    startMs = Date.now();
    chars = 0;
  }

  function handleUpdate(event: any) {
    if (startMs === null) return;
    const ev: any = (event as any).assistantMessageEvent;
    if (!ev) return;
    if (typeof ev.delta === "string") {
      chars += ev.delta.length;
    } else if (ev.type === "text_delta" || ev.type === "thinking_delta") {
      chars += String(ev.delta ?? "").length;
    } else if (ev.type === "thinking" && typeof ev.thinking === "string") {
      chars += ev.thinking.length;
    }
  }

  function handleEnd(event: any) {
    const msg: any = event.message as any;
    if (!msg || msg.role !== "assistant") return;
    if (startMs === null) return;
    const dur = Date.now() - startMs;
    const usage = msg.usage;
    const tokens =
      typeof usage?.output === "number" && usage.output > 0
        ? usage.output
        : Math.round(chars / CHARS_PER_TOKEN);
    const thinkingText: string = (msg.content ?? [])
      .filter(
        (c: any) => c.type === "thinking" && typeof c.thinking === "string",
      )
      .map((c: any) => c.thinking)
      .join("\n\n")
      .trim();
    const title = thinkingText ? extractTitle(thinkingText) : "";
    const ts: number =
      typeof msg.timestamp === "number" ? msg.timestamp : Date.now();
    metaByTs.set(ts, { durationMs: dur, tokens, title });
    // keep map bounded
    if (metaByTs.size > 100) {
      const first = metaByTs.keys().next().value;
      if (first !== undefined) metaByTs.delete(first);
    }
    startMs = null;
    chars = 0;
  }

  pi.on("message_start", handleStart as any);
  pi.on("message_update", handleUpdate as any);
  pi.on("message_end", handleEnd as any);

  // ---- tool execution timing state (for collapsed log lines) ----
  const toolStartByCallId = new Map<string, number>();
  const toolDurByCallId = new Map<string, number>();
  function boundMap(m: Map<string, number>, max = 200) {
    while (m.size > max) {
      const first = m.keys().next().value;
      if (first === undefined) break;
      m.delete(first);
    }
  }
  pi.on("tool_execution_start", (event: any) => {
    try {
      toolStartByCallId.set(String(event.toolCallId), Date.now());
      boundMap(toolStartByCallId);
    } catch {}
  });
  pi.on("tool_execution_end", (event: any) => {
    try {
      const id = String(event.toolCallId);
      const startedAt = toolStartByCallId.get(id);
      if (startedAt !== undefined) {
        toolDurByCallId.set(id, Math.max(0, Date.now() - startedAt));
        toolStartByCallId.delete(id);
        boundMap(toolDurByCallId);
      }
    } catch {}
  });

  // ---- sync thoughtsExpanded with global tool expand toggle ----
  (async () => {
    try {
      const root = findPiRoot();
      if (!root) return;
      const path = require("node:path") as typeof import("node:path");
      const { pathToFileURL } =
        require("node:url") as typeof import("node:url");
      const url = pathToFileURL(
        path.join(root, "dist/modes/interactive/interactive-mode.js"),
      ).href;
      const mod: any = await import(url);
      // Patch prototype so any instance reflects it; also patch current instance if we can grab it
      const proto: any =
        mod.InteractiveMode?.prototype ?? mod.default?.prototype;
      if (proto && !proto.__agyThinkingPatched) {
        const origSet = proto.setToolsExpanded;
        if (typeof origSet === "function") {
          proto.setToolsExpanded = function (expanded: boolean) {
            thoughtsExpanded = expanded;
            return origSet.call(this, expanded);
          };
          proto.__agyThinkingPatched = true;
        }
        const origToggle = proto.toggleToolOutputExpansion;
        if (
          typeof origToggle === "function" &&
          !proto.__agyThinkingTogglePatched
        ) {
          proto.toggleToolOutputExpansion = function () {
            // flip our flag in lockstep; setToolsExpanded will set it again to same value, that's fine
            thoughtsExpanded = !thoughtsExpanded;
            return origToggle.call(this);
          };
          proto.__agyThinkingTogglePatched = true;
        }
      }
    } catch (e) {
      // best-effort; thinking still collapses just not synced to ctrl+o
      // eslint-disable-next-line no-console
      console.error("[agy-thinking] expand sync patch failed", e);
    }
  })();

  // ---- patch AssistantMessageComponent.updateContent to collapse thinking ----
  (async () => {
    let theme: any = null;
    let Markdown: any = null;
    let Text: any = null;
    let Container: any = null;
    let Spacer: any = null;
    let createMarkdownTransform: any = null;
    let getMarkdownTheme: any = null;

    try {
      const root = findPiRoot();
      if (!root) return;

      const path = require("node:path") as typeof import("node:path");
      const { pathToFileURL } =
        require("node:url") as typeof import("node:url");

      // theme
      try {
        const themeUrl = pathToFileURL(
          path.join(root, "dist/modes/interactive/theme/theme.js"),
        ).href;
        const themeMod: any = await import(themeUrl);
        theme = themeMod.theme ?? themeMod.default ?? null;
      } catch {}

      // tui components — try pi-tui import first (jiti can resolve it)
      try {
        const tuiMod: any = require("@earendil-works/pi-tui");
        Container = tuiMod.Container;
        Markdown = tuiMod.Markdown;
        Text = tuiMod.Text;
        Spacer = tuiMod.Spacer;
      } catch {
        // fallback via file url for pi-tui dist
        try {
          const piTuiRoot = path.join(
            root,
            "node_modules/@earendil-works/pi-tui/dist/index.js",
          );
          const tuiUrl = pathToFileURL(piTuiRoot).href;
          const tuiMod: any = await import(tuiUrl);
          Container = tuiMod.Container;
          Markdown = tuiMod.Markdown;
          Text = tuiMod.Text;
          Spacer = tuiMod.Spacer;
        } catch {}
      }

      // markdown helpers
      try {
        const mdUrl = pathToFileURL(
          path.join(
            root,
            "dist/modes/interactive/components/markdown-transform.js",
          ),
        ).href;
        const mdMod: any = await import(mdUrl);
        createMarkdownTransform = mdMod.createMarkdownTransform;
      } catch {}
      try {
        const thUrl = pathToFileURL(
          path.join(root, "dist/modes/interactive/theme/theme.js"),
        ).href;
        const thMod: any = await import(thUrl);
        getMarkdownTheme = thMod.getMarkdownTheme;
      } catch {}

      const compUrl = pathToFileURL(
        path.join(
          root,
          "dist/modes/interactive/components/assistant-message.js",
        ),
      ).href;
      const compMod: any = await import(compUrl);
      const Ctor: any = compMod.AssistantMessageComponent;
      if (!Ctor || Ctor.prototype.__agyThinkingPatched) return;

      const origUpdate = Ctor.prototype.updateContent;

      Ctor.prototype.updateContent = function (message: any, isStreaming: any) {
        // Keep isStreaming type as original expects (boolean)
        const streaming = !!isStreaming;

        // For non-thinking or streaming, delegate to original (preserves timestamps patch if it wrapped orig)
        const hasThinking =
          !streaming &&
          message &&
          Array.isArray(message.content) &&
          message.content.some(
            (c: any) =>
              c.type === "thinking" && String(c.thinking ?? "").trim(),
          );

        if (!hasThinking) {
          return origUpdate.call(this, message, streaming);
        }

        // If hideThinkingBlock is on, let original handle it (shows static label)
        if (this.hideThinkingBlock) {
          return origUpdate.call(this, message, streaming);
        }

        // ---- custom rendering with agy-style collapsed thinking ----
        this.lastMessage = message;
        this.isStreaming = streaming;
        // @ts-expect-error — contentContainer is set in ctor
        this.contentContainer.clear();

        const hasVisibleContent = message.content.some(
          (c: any) =>
            (c.type === "text" && String(c.text ?? "").trim()) ||
            (c.type === "thinking" && String(c.thinking ?? "").trim()),
        );
        if (hasVisibleContent) {
          this.contentContainer.addChild(new Spacer(1));
        }

        // Walk content in order; group consecutive thinking blocks like original does
        for (let i = 0; i < message.content.length; i++) {
          const content: any = message.content[i];
          if (content.type === "text" && String(content.text ?? "").trim()) {
            const mdTheme =
              this.markdownTheme ??
              (typeof getMarkdownTheme === "function"
                ? getMarkdownTheme()
                : undefined);
            const transformers = this.markdownTransformers ?? [];
            const tf =
              typeof createMarkdownTransform === "function"
                ? createMarkdownTransform("assistant", streaming, transformers)
                : undefined;
            this.contentContainer.addChild(
              new Markdown(
                String(content.text).trim(),
                this.outputPad,
                0,
                mdTheme,
                undefined,
                {
                  transform: tf,
                },
              ),
            );
          } else if (content.type === "thinking") {
            // Collect run of thinking blocks
            const thinkingBlocks: string[] = [];
            for (; i < message.content.length; i++) {
              const tc: any = message.content[i];
              if (tc.type !== "thinking") break;
              const t = String(tc.thinking ?? "").trim();
              if (t) thinkingBlocks.push(t);
            }
            i--;
            if (!thinkingBlocks.length) continue;

            const hasVisibleAfter = message.content
              .slice(i + 1)
              .some(
                (c: any) =>
                  (c.type === "text" && String(c.text ?? "").trim()) ||
                  (c.type === "thinking" && String(c.thinking ?? "").trim()),
              );

            const thinkingText = thinkingBlocks.join("\n\n");
            // Lookup timing meta
            const ts: number | undefined =
              typeof message.timestamp === "number"
                ? message.timestamp
                : undefined;
            let meta = ts === undefined ? undefined : metaByTs.get(ts);
            if (!meta) {
              // fallback: compute from thinkingText length and a plausible 2-4s
              const tok = Math.max(
                1,
                Math.round(thinkingText.length / CHARS_PER_TOKEN),
              );
              const dur = Math.max(
                800,
                Math.min(8000, Math.round((tok / 45) * 1000)),
              ); // ~45 tok/s heuristic
              const title = extractTitle(thinkingText);
              meta = { durationMs: dur, tokens: tok, title };
            }

            const durStr = formatDuration(meta.durationMs);
            const tokStr = `${meta.tokens} tokens`;
            // agy header: ▸ Thought for 3s, 673 tokens  (muted) + expand hint when collapsed
            const tTheme = theme ??
              this.markdownTheme ?? {
                fg: (_: string, s: string) => s,
                bold: (s: string) => s,
                italic: (s: string) => s,
              };
            const headerBase = `▸ Thought for ${durStr}, ${tokStr}`;
            const hint = thoughtsExpanded
              ? ""
              : tTheme.fg("muted", " (ctrl+o to expand)");
            const headerLine = tTheme.fg("muted", headerBase) + hint;

            // Use a Container to group header + title + optional full body
            const thinkContainer = new Container();
            thinkContainer.addChild(new Text(headerLine, this.outputPad, 0));
            if (meta.title) {
              // plain/default color so it reads white like assistant response text
              thinkContainer.addChild(
                new Text(`  ${meta.title}`, this.outputPad, 0),
              );
            }

            if (thoughtsExpanded) {
              const mdTheme =
                this.markdownTheme ??
                (typeof getMarkdownTheme === "function"
                  ? getMarkdownTheme()
                  : undefined);
              const transformers = this.markdownTransformers ?? [];
              const tf =
                typeof createMarkdownTransform === "function"
                  ? createMarkdownTransform(
                      "assistant-thinking",
                      streaming,
                      transformers,
                    )
                  : undefined;
              // Render full thinking in default color (white), matching response text
              const mdThemeThinking = mdTheme;
              thinkContainer.addChild(
                new Markdown(
                  thinkingText,
                  this.outputPad,
                  0,
                  mdThemeThinking,
                  undefined,
                  {
                    transform: tf,
                  },
                ),
              );
            }

            this.contentContainer.addChild(thinkContainer);

            if (hasVisibleAfter) {
              this.contentContainer.addChild(new Spacer(1));
            }
          }
        }

        // Replicate original's stopReason handling
        const hasToolCalls = message.content.some(
          (c: any) => c.type === "toolCall",
        );
        // @ts-expect-error
        this.hasToolCalls = hasToolCalls;
        const tTheme2 = theme ?? { fg: (_: string, s: string) => s };
        if (message.stopReason === "length") {
          this.contentContainer.addChild(new Spacer(1));
          this.contentContainer.addChild(
            new Text(
              tTheme2.fg("error", "Response was truncated before completion."),
              this.outputPad,
              0,
            ),
          );
        } else if (!hasToolCalls) {
          if (message.stopReason === "aborted") {
            const abortMessage =
              message.errorMessage &&
              message.errorMessage !== "Request was aborted"
                ? message.errorMessage
                : "Operation aborted";
            this.contentContainer.addChild(new Spacer(1));
            this.contentContainer.addChild(
              new Text(tTheme2.fg("error", abortMessage), this.outputPad, 0),
            );
          } else if (message.stopReason === "error") {
            const errorMsg = message.errorMessage || "Unknown error";
            this.contentContainer.addChild(new Spacer(1));
            this.contentContainer.addChild(
              new Text(
                tTheme2.fg("error", `Error: ${errorMsg}`),
                this.outputPad,
                0,
              ),
            );
          }
        }

        // ---- preserve timestamps.ts behavior: inject [HH:MM:SS] header if we have a timestamp ----
        // Timestamps extension also patches updateContent to prepend a dim time line.
        // Since we bypassed origUpdate for thinking messages, we need to re-add it here best-effort.
        try {
          const ts: number | undefined =
            typeof message.timestamp === "number"
              ? message.timestamp
              : undefined;
          if (ts) {
            const label = `${(theme ?? { fg: (_: string, s: string) => s }).fg("dim", `[${formatTime(ts)}]`)} ${(theme ?? { fg: (_: string, s: string) => s }).fg("muted", "assistant")}`;
            // Insert as first child of contentContainer (before spacer if present)
            const pad = typeof this.outputPad === "number" ? this.outputPad : 1;
            const stamp = new Text(label, pad, 0);
            const existing = [...this.contentContainer.children];
            this.contentContainer.clear();
            this.contentContainer.addChild(stamp);
            for (const c of existing) this.contentContainer.addChild(c);
          }
        } catch {}
      };

      Ctor.prototype.__agyThinkingPatched = true;
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error("[agy-thinking] patch failed", e);
    }
  })();

  // ---- patch generic tool fallback to use agy bullet (covers ManageTask etc.) ----
  (async () => {
    try {
      const root = findPiRoot();
      if (!root) return;
      const path = require("node:path") as typeof import("node:path");
      const { pathToFileURL } =
        require("node:url") as typeof import("node:url");
      const url = pathToFileURL(
        path.join(root, "dist/modes/interactive/components/tool-execution.js"),
      ).href;
      const mod: any = await import(url);
      const ToolCtor: any = mod.ToolExecutionComponent;
      if (!ToolCtor || ToolCtor.prototype.__agyBulletPatched) return;
      let themeLocal: any = null;
      try {
        const themeUrl = pathToFileURL(
          path.join(root, "dist/modes/interactive/theme/theme.js"),
        ).href;
        const themeMod: any = await import(themeUrl);
        themeLocal = themeMod.theme ?? themeMod.default ?? null;
      } catch {}
      const origFallback = ToolCtor.prototype.createCallFallback;
      ToolCtor.prototype.createCallFallback = function () {
        try {
          const t = themeLocal ?? {
            fg: (_: string, s: string) => s,
            bold: (s: string) => s,
          };
          const bullet = t.fg("accent", "● ");
          const name = t.fg(
            "toolTitle",
            t.bold(String(this.toolName ?? "tool")),
          );
          let argStr = "";
          try {
            const a: any = this.args;
            if (a && typeof a === "object") {
              const vals = Object.values(a);
              const first = vals.find(
                (v) => typeof v === "string" && String(v).trim(),
              ) as string | undefined;
              if (first) argStr = `(${truncated(String(first), 72)})`;
              else if (vals.length) {
                const j = truncated(JSON.stringify(vals[0]), 72);
                if (j && j !== '""' && j !== "null") argStr = `(${j})`;
              }
            } else if (typeof a === "string" && a.trim()) {
              argStr = `(${truncated(a, 72)})`;
            }
          } catch {}
          const argPart = argStr ? t.fg("accent", argStr) : "";
          const TextCtor = require("@earendil-works/pi-tui").Text as any;
          if (TextCtor) return new TextCtor(bullet + name + argPart, 0, 0);
        } catch {}
        return origFallback.call(this);
      };
      ToolCtor.prototype.__agyBulletPatched = true;
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error("[agy-thinking] bullet patch failed", e);
    }
  })();

  // ---- collapse tool calls to agy-style muted log lines ----
  // Two-line minimal: header `▸ bash · cmd · ✓ 2.1s` + first output line.
  // Errors keep the original red block; ctrl+o expands to full boxed output.
  (async () => {
    try {
      const root = findPiRoot();
      if (!root) return;
      const path = require("node:path") as typeof import("node:path");
      const { pathToFileURL } =
        require("node:url") as typeof import("node:url");
      const url = pathToFileURL(
        path.join(root, "dist/modes/interactive/components/tool-execution.js"),
      ).href;
      const mod: any = await import(url);
      const Ctor2: any = mod.ToolExecutionComponent;
      if (!Ctor2 || Ctor2.prototype.__agyCollapsePatched) return;

      let themeLocal: any = null;
      try {
        const themeUrl = pathToFileURL(
          path.join(root, "dist/modes/interactive/theme/theme.js"),
        ).href;
        const themeMod: any = await import(themeUrl);
        themeLocal = themeMod.theme ?? themeMod.default ?? null;
      } catch {}
      let TextCtor: any = null;
      try {
        TextCtor = require("@earendil-works/pi-tui").Text;
      } catch {}

      const origUpdateDisplay = Ctor2.prototype.updateDisplay;
      const shellChildOf = (self: any) =>
        self.hasRendererDefinition()
          ? self.getRenderShell() === "self"
            ? self.selfRenderContainer
            : self.contentBox
          : self.contentText;

      Ctor2.prototype.updateDisplay = function () {
        const t = themeLocal ?? { fg: (_k: string, s: string) => s };
        const expanded = !!this.expanded;
        const isError = !!this.result?.isError;

        // Expanded view or errors: original boxed rendering (red bg preserved)
        if (expanded || isError || !TextCtor) {
          if (this.__agyCollapsed) {
            // restore constructor-time structure before delegating
            this.clear();
            this.addChild(shellChildOf(this));
            this.__agyCollapsed = false;
          }
          return origUpdateDisplay.call(this);
        }

        // Collapsed: replace box with muted log lines
        this.hideComponent = false;
        this.clear();
        try {
          this.selfRenderContainer.clear();
        } catch {}
        try {
          this.contentBox.clear();
        } catch {}

        const dur = toolDurByCallId.get(String(this.toolCallId ?? ""));
        const running = this.isPartial || !this.result;
        const argSummary = summarizeToolArgs(this.args);
        const status = running
          ? "…"
          : `✓${dur === undefined ? "" : ` ${formatDuration(dur)}`}`;
        const header = truncated(
          `▸ ${String(this.toolName ?? "tool")}${argSummary ? ` · ${argSummary}` : ""} · ${status}`,
          110,
        );
        this.addChild(new TextCtor(t.fg("muted", header), 0, 0));
        const outLine = running ? "" : firstOutputLine(this.result);
        if (outLine) {
          this.addChild(new TextCtor(t.fg("dim", `  ${outLine}`), 0, 0));
        }
        this.__agyCollapsed = true;
      };

      Ctor2.prototype.__agyCollapsePatched = true;
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error("[agy-thinking] tool collapse patch failed", e);
    }
  })();
}
