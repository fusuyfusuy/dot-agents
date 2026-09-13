// Compact tool rendering: keeps the chat transcript readable by showing tool
// calls as one-line summaries by default (truncated command, diff stats,
// line/hit counts). Full detail is one key away: focus a tool row and press the
// expand key (default ctrl+o).
//
// Execution is untouched — each built-in tool is re-registered with the same
// name and delegates execute() to the original implementation, so the agent
// behaves exactly as before; only the TUI rendering is compact.
//
// Install: copy to ~/.pi/agent/extensions/ (like gated-tools.ts) and run
// /reload-runtime, or restart pi.
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import {
  createBashTool,
  createEditTool,
  createFindTool,
  createGrepTool,
  createLsTool,
  createReadTool,
  createWriteTool,
  type BashToolDetails,
  type EditToolDetails,
} from "@earendil-works/pi-coding-agent";
import { Text } from "@earendil-works/pi-tui";

const CALL_CHARS = 80; // max command/pattern chars shown in the collapsed header
const EXPANDED_LINES = 20; // lines shown in expanded view per tool

function truncated(s: string, max: number, suffix = "..."): string {
  if (s.length <= max) return s;
  return s.slice(0, max - suffix.length) + suffix;
}

function contentLines(content: string): number {
  return content.split("\n").filter((l) => l.trim()).length;
}

function prettyLines(text: string, theme: any, max = EXPANDED_LINES): string {
  const lines = text.split("\n");
  const shown = lines.slice(0, max);
  let out = "";
  for (const line of shown) out += `\n${theme.fg("dim", line)}`;
  if (lines.length > max)
    out += `\n${theme.fg("muted", `... ${lines.length - max} more lines`)}`;
  return out;
}

function expandHint(theme: any): string {
  return theme.fg("muted", ` (ctrl+o to expand)`);
}

function bullet(theme: any): string {
  return theme.fg("accent", "● ");
}

function agyHeader(theme: any, name: string, arg: string): string {
  // agy style: ● Read(path)  /  ● Bash(command)  — bullet + Capitalized name + parens
  return (
    bullet(theme) +
    theme.fg("toolTitle", theme.bold(name)) +
    theme.fg("accent", `(${arg})`)
  );
}

export default function (pi: ExtensionAPI) {
  const cwd = process.cwd();

  // --- bash: one-line command, exit code + line count; output on expand ---
  const originalBash = createBashTool(cwd);
  pi.registerTool({
    name: "bash",
    label: "bash",
    description: originalBash.description,
    parameters: originalBash.parameters,
    async execute(toolCallId, params, signal, onUpdate) {
      return originalBash.execute(toolCallId, params, signal, onUpdate);
    },
    renderCall(args, theme, _context) {
      const cmd = args.command.replace(/\s*\n\s*/g, " ").trim();
      const arg = truncated(cmd, CALL_CHARS);
      let text = agyHeader(theme, "Bash", arg);
      if (args.timeout) text += theme.fg("dim", ` timeout:${args.timeout}s`);
      return new Text(text, 0, 0);
    },
    renderResult(result, { expanded, isPartial }, theme, _context) {
      if (isPartial) return new Text(theme.fg("warning", "Running..."), 0, 0);
      const details = result.details as BashToolDetails | undefined;
      const content = result.content[0];
      const output = content?.type === "text" ? content.text : "";
      const exitMatch = output.match(/exit code: (\d+)/);
      const exitCode = exitMatch ? parseInt(exitMatch[1], 10) : null;
      const lineCount = contentLines(output);

      let text = exitCode
        ? theme.fg("error", `exit ${exitCode}`)
        : theme.fg("success", "done");
      text += theme.fg("dim", ` (${lineCount} lines)`);
      if (details?.truncation?.truncated)
        text += theme.fg("warning", " [truncated]");
      if (!expanded) {
        if (lineCount > 1) text += ` ${expandHint(theme)}`;
        return new Text(text, 0, 0);
      }
      return new Text(
        text + prettyLines(output.replace(/\n*exit code: \d+$/m, ""), theme),
        0,
        0,
      );
    },
  });

  // --- read: path + line count; first lines on expand ---
  const originalRead = createReadTool(cwd);
  pi.registerTool({
    name: "read",
    label: "read",
    description: originalRead.description,
    parameters: originalRead.parameters,
    async execute(toolCallId, params, signal, onUpdate) {
      return originalRead.execute(toolCallId, params, signal, onUpdate);
    },
    renderCall(args, theme, _context) {
      const path = truncated(args.path, CALL_CHARS);
      let text = agyHeader(theme, "Read", path);
      const parts: string[] = [];
      if (args.offset) parts.push(`offset=${args.offset}`);
      if (args.limit) parts.push(`limit=${args.limit}`);
      if (parts.length) text += theme.fg("dim", ` ${parts.join(", ")}`);
      return new Text(text, 0, 0);
    },
    renderResult(result, { expanded, isPartial }, theme, _context) {
      if (isPartial) return new Text(theme.fg("warning", "Reading..."), 0, 0);
      const details = result.details as
        | { truncation?: { truncated?: boolean; totalLines?: number } }
        | undefined;
      const content = result.content[0];
      if (content?.type === "image")
        return new Text(theme.fg("success", "Image loaded"), 0, 0);
      if (content?.type !== "text")
        return new Text(theme.fg("error", "No content"), 0, 0);

      const lineCount = content.text.split("\n").length;
      let text = theme.fg("success", `${lineCount} lines`);
      if (details?.truncation?.truncated) {
        text += theme.fg(
          "warning",
          ` (truncated from ${details.truncation.totalLines})`,
        );
      }
      if (!expanded) return new Text(text, 0, 0);
      return new Text(text + prettyLines(content.text, theme), 0, 0);
    },
  });

  // --- edit: path + diff stats; diff on expand ---
  const originalEdit = createEditTool(cwd);
  pi.registerTool({
    name: "edit",
    label: "edit",
    description: originalEdit.description,
    parameters: originalEdit.parameters,
    async execute(toolCallId, params, signal, onUpdate) {
      return originalEdit.execute(toolCallId, params, signal, onUpdate);
    },
    renderCall(args, theme, _context) {
      const path = truncated(args.path, CALL_CHARS);
      return new Text(agyHeader(theme, "Edit", path), 0, 0);
    },
    renderResult(result, { expanded, isPartial }, theme, _context) {
      if (isPartial) return new Text(theme.fg("warning", "Editing..."), 0, 0);
      const details = result.details as EditToolDetails | undefined;
      const content = result.content[0];
      if (content?.type === "text" && content.text.startsWith("Error")) {
        return new Text(theme.fg("error", content.text.split("\n")[0]), 0, 0);
      }
      if (!details?.diff) return new Text(theme.fg("success", "Applied"), 0, 0);

      const diffLines = details.diff.split("\n");
      let additions = 0;
      let removals = 0;
      for (const line of diffLines) {
        if (line.startsWith("+") && !line.startsWith("+++")) additions++;
        if (line.startsWith("-") && !line.startsWith("---")) removals++;
      }
      let text = theme.fg("success", `+${additions}`);
      text += theme.fg("dim", " / ");
      text += theme.fg("error", `-${removals}`);

      if (!expanded) {
        if (diffLines.length > 3) text += ` ${expandHint(theme)}`;
        return new Text(text, 0, 0);
      }
      for (const line of diffLines.slice(0, EXPANDED_LINES)) {
        if (line.startsWith("+") && !line.startsWith("+++"))
          text += `\n${theme.fg("success", line)}`;
        else if (line.startsWith("-") && !line.startsWith("---"))
          text += `\n${theme.fg("error", line)}`;
        else text += `\n${theme.fg("dim", line)}`;
      }
      if (diffLines.length > EXPANDED_LINES) {
        text += `\n${theme.fg("muted", `... ${diffLines.length - EXPANDED_LINES} more diff lines`)}`;
      }
      return new Text(text, 0, 0);
    },
  });

  // --- write: path + line count; one-line result ---
  const originalWrite = createWriteTool(cwd);
  pi.registerTool({
    name: "write",
    label: "write",
    description: originalWrite.description,
    parameters: originalWrite.parameters,
    async execute(toolCallId, params, signal, onUpdate) {
      return originalWrite.execute(toolCallId, params, signal, onUpdate);
    },
    renderCall(args, theme, _context) {
      const path = truncated(args.path, CALL_CHARS);
      let text = agyHeader(theme, "Write", path);
      const lineCount = args.content.split("\n").length;
      text += theme.fg("dim", ` ${lineCount} lines`);
      return new Text(text, 0, 0);
    },
    renderResult(result, { isPartial }, theme, _context) {
      if (isPartial) return new Text(theme.fg("warning", "Writing..."), 0, 0);
      const content = result.content[0];
      if (content?.type === "text" && content.text.startsWith("Error")) {
        return new Text(theme.fg("error", content.text.split("\n")[0]), 0, 0);
      }
      return new Text(theme.fg("success", "Written"), 0, 0);
    },
  });

  // --- grep: pattern; match count; matches on expand ---
  const originalGrep = createGrepTool(cwd);
  pi.registerTool({
    name: "grep",
    label: "grep",
    description: originalGrep.description,
    parameters: originalGrep.parameters,
    async execute(toolCallId, params, signal, onUpdate) {
      return originalGrep.execute(toolCallId, params, signal, onUpdate);
    },
    renderCall(args, theme, _context) {
      const pat = truncated(args.pattern, CALL_CHARS);
      // agy shows Search(pattern) — keep Grep label but mimic parens style
      let text = agyHeader(theme, "Grep", `"${pat}"`);
      if (args.path) text += theme.fg("muted", ` in ${args.path}`);
      if (args.limit) text += theme.fg("dim", ` limit:${args.limit}`);
      return new Text(text, 0, 0);
    },
    renderResult(result, { expanded, isPartial }, theme, _context) {
      if (isPartial) return new Text(theme.fg("warning", "Searching..."), 0, 0);
      const content = result.content[0];
      const output = content?.type === "text" ? content.text.trim() : "";
      if (!output) return new Text(theme.fg("dim", "No matches"), 0, 0);
      const matchCount = contentLines(output);
      let text = theme.fg("success", `${matchCount} matches`);
      if (!expanded) {
        if (matchCount > 1) text += ` ${expandHint(theme)}`;
        return new Text(text, 0, 0);
      }
      return new Text(text + prettyLines(output, theme), 0, 0);
    },
  });

  // --- find: pattern + file count; list on expand ---
  const originalFind = createFindTool(cwd);
  pi.registerTool({
    name: "find",
    label: "find",
    description: originalFind.description,
    parameters: originalFind.parameters,
    async execute(toolCallId, params, signal, onUpdate) {
      return originalFind.execute(toolCallId, params, signal, onUpdate);
    },
    renderCall(args, theme, _context) {
      const pat = truncated(args.pattern, CALL_CHARS);
      let text = agyHeader(theme, "Find", `"${pat}"`);
      if (args.path) text += theme.fg("muted", ` in ${args.path}`);
      return new Text(text, 0, 0);
    },
    renderResult(result, { expanded, isPartial }, theme, _context) {
      if (isPartial) return new Text(theme.fg("warning", "Finding..."), 0, 0);
      const content = result.content[0];
      const output = content?.type === "text" ? content.text : "";
      if (!output.trim()) return new Text(theme.fg("dim", "No files"), 0, 0);
      const count = contentLines(output);
      let text = theme.fg("success", `${count} file${count === 1 ? "" : "s"}`);
      if (!expanded) {
        if (count > 1) text += ` ${expandHint(theme)}`;
        return new Text(text, 0, 0);
      }
      return new Text(text + prettyLines(output, theme), 0, 0);
    },
  });

  // --- ls: one-line entry count; listing on expand ---
  const originalLs = createLsTool(cwd);
  pi.registerTool({
    name: "ls",
    label: "ls",
    description: originalLs.description,
    parameters: originalLs.parameters,
    async execute(toolCallId, params, signal, onUpdate) {
      return originalLs.execute(toolCallId, params, signal, onUpdate);
    },
    renderCall(args, theme, _context) {
      const p = truncated(args.path || ".", CALL_CHARS);
      let text = agyHeader(theme, "Ls", p);
      if (args.limit) text += theme.fg("dim", ` limit:${args.limit}`);
      return new Text(text, 0, 0);
    },
    renderResult(result, { expanded, isPartial }, theme, _context) {
      if (isPartial) return new Text(theme.fg("warning", "Listing..."), 0, 0);
      const content = result.content[0];
      const output = content?.type === "text" ? content.text : "";
      if (!output.trim()) return new Text(theme.fg("dim", "Empty"), 0, 0);
      const count = contentLines(output);
      let text = theme.fg(
        "success",
        `${count} entr${count === 1 ? "y" : "ies"}`,
      );
      if (!expanded) {
        if (count > 1) text += ` ${expandHint(theme)}`;
        return new Text(text, 0, 0);
      }
      return new Text(text + prettyLines(output, theme), 0, 0);
    },
  });
}
