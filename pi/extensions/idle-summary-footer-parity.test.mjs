// Parity test: idle-summary.ts inline footer wraps pi's real FooterComponent
// with a shim session. Asserts the wrapped render matches the unwrapped stock
// render exactly (minus the injected sentence on line 0) and hides FOOTER_KEY.
// Run: node idle-summary-footer-parity.test.mjs
import { createRequire } from "node:module";
import { execSync } from "node:child_process";
const require = createRequire(import.meta.url);
const PI =
	process.env.PI_DIR ||
	`${execSync("npm root -g").toString().trim()}/@earendil-works/pi-coding-agent`;
const { FooterComponent } = require(`${PI}/dist/index.js`);
require(`${PI}/dist/modes/interactive/theme/theme.js`).initTheme();
const { visibleWidth } = require(`${PI}/node_modules/@earendil-works/pi-tui`);

const FOOTER_KEY = "idle-summary-footer";

const usage = {
	input: 1500,
	output: 500,
	cacheRead: 100_000,
	cacheWrite: 2000,
	cost: { total: 0.042 },
};
const entries = [{ type: "message", message: { role: "assistant", usage } }];
const sessionManager = {
	getEntries: () => entries,
	getCwd: () => "/path/to/demo",
	getSessionName: () => "fix-footer",
};
const state = {
	model: {
		id: "gpt-x",
		provider: "openai",
		contextWindow: 200_000,
		reasoning: true,
	},
	thinkingLevel: "medium",
};
const shimSession = {
	state,
	sessionManager,
	getContextUsage: () => ({
		tokens: 24_600,
		contextWindow: 200_000,
		percent: 12.3,
	}),
	modelRuntime: { isUsingSubscription: () => false },
};
const statuses = new Map([
	[FOOTER_KEY, "last: testing sentence"],
	["other-ext", "⏸ plan"],
]);
const makeFooterData = () => ({
	getGitBranch: () => "main",
	getExtensionStatuses: () => new Map(statuses),
	getAvailableProviderCount: () => 2,
});

// stock, untouched
const stockLines = new FooterComponent(shimSession, makeFooterData()).render(
	120,
);

// wrapped exactly like installInlineFooter does
const footerData = makeFooterData();
const wrappedFooterData = Object.create(footerData);
wrappedFooterData.getExtensionStatuses = () => {
	const m = new Map(footerData.getExtensionStatuses());
	m.delete(FOOTER_KEY);
	return m;
};
const wrapped = new FooterComponent(shimSession, wrappedFooterData);
const lines = [...wrapped.render(120)];

// inject sentence like installInlineFooter.render does
let truncateToWidth;
try {
	truncateToWidth = require("@earendil-works/pi-tui").truncateToWidth;
} catch {}
if (truncateToWidth) {
	const pwdLine = lines[0] + ` • User asked about footer parity`;
	lines[0] = truncateToWidth(pwdLine, 120, "...");
}

const strip = (s) => s.replaceAll(" • User asked about footer parity", "");

const assert = (cond, msg) => {
	if (!cond) {
		console.error("FAIL:", msg);
		process.exit(1);
	}
	console.log("ok:", msg);
};

assert(lines.length === stockLines.length, `same line count (${lines.length})`);
assert(
	strip(lines[0]) === stockLines[0],
	"line 0 (pwd) identical after removing injection",
);
assert(lines[1] === stockLines[1], "line 1 (stats) byte-identical to stock");
assert(
	!lines.join("\n").includes(FOOTER_KEY) &&
		!lines.some((l) => l.includes("last:")),
	"FOOTER_KEY status hidden",
);
assert(
	lines[0].includes("(main)") && lines[0].includes("fix-footer"),
	"branch + session name present",
);
assert(
	lines[1].includes("↑1.5k") &&
		lines[1].includes("↓500") &&
		lines[1].includes("R100k") &&
		lines[1].includes("W2.0k") &&
		lines[1].includes("$0.042"),
	"full token/cache/cost stats rendered",
);
assert(
	lines[1].includes("12.3%/200k (auto)"),
	"context % / window / auto indicator rendered",
);
assert(/CH\d+\.\d%/.test(lines[1]), "cache-hit rate rendered");
assert(
	lines.some((l) => l.includes("⏸ plan")) &&
		!lines.some((l) => l.includes("last:")),
	"foreign status kept",
);
assert(visibleWidth(strip(lines[1])) <= 120, "stats line within width");
console.log("\nALL PASS");
