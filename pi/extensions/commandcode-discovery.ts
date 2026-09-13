// @ts-nocheck — pi extension runs via jiti
// CommandCode model discovery for pi.
//
// `commandcode` is a custom provider: its model list lives entirely in
// ~/.pi/agent/models.json, and pi's `refreshModels` catalog only covers
// built-in providers. pi.registerProvider(name, { models }) REPLACES the
// provider's list, so this extension re-registers the curated entries
// verbatim and appends ids the endpoint reports that the registry does not
// know yet. It no-ops when nothing is new and fails open on any error, so
// models.json stays the authority whenever discovery cannot run.
//
// Endpoint: https://api.commandcode.ai/provider/v1/models (id/name/context_length only)
import type { ExtensionAPI, ProviderModelConfig } from "@earendil-works/pi-coding-agent";
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const PROVIDER = "commandcode";
const BASE_URL = "https://api.commandcode.ai/provider/v1";
const MODELS_JSON = join(homedir(), ".pi", "agent", "models.json");
const DEFAULT_CONTEXT_WINDOW = 128000;
const DEFAULT_MAX_TOKENS = 16384;

interface CommandCodeModelEntry {
  id?: string;
  name?: string;
  context_length?: number;
}

function readCuratedProvider(): Record<string, any> | null {
  try {
    const config = JSON.parse(readFileSync(MODELS_JSON, "utf8"));
    const provider = config?.providers?.[PROVIDER];
    return provider && Array.isArray(provider.models) && provider.models.length > 0
      ? provider
      : null;
  } catch {
    return null;
  }
}

function resolveApiKey(): string {
  if (process.env.COMMANDCODE_API_KEY?.trim()) {
    return process.env.COMMANDCODE_API_KEY.trim();
  }
  try {
    const stored = JSON.parse(readFileSync(join(homedir(), ".pi", "agent", "auth.json"), "utf8"))
      ?.[PROVIDER]?.key;
    return typeof stored === "string" ? stored.trim() : "";
  } catch {
    return "";
  }
}

async function discoverLiveModels(apiKey: string): Promise<CommandCodeModelEntry[]> {
  try {
    const res = await fetch(`${BASE_URL}/models`, {
      headers: apiKey ? { Authorization: `Bearer ${apiKey}` } : {},
      signal: AbortSignal.timeout(3000),
    });
    if (!res.ok) return [];
    const payload = (await res.json()) as { data?: CommandCodeModelEntry[] };
    return Array.isArray(payload?.data) ? payload.data : [];
  } catch {
    return [];
  }
}

function toModelConfig(entry: CommandCodeModelEntry): ProviderModelConfig | null {
  const id = typeof entry?.id === "string" ? entry.id.trim() : "";
  if (!id) return null;
  const isDeepSeek = id.toLowerCase().includes("deepseek");
  return {
    id,
    name: entry.name?.trim() || id,
    reasoning: true,
    input: ["text"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: entry.context_length ?? DEFAULT_CONTEXT_WINDOW,
    maxTokens: DEFAULT_MAX_TOKENS,
    ...(isDeepSeek
      ? {
          compat: {
            requiresReasoningContentOnAssistantMessages: true,
            thinkingFormat: "deepseek",
          },
        }
      : {}),
  };
}

export default async function (pi: ExtensionAPI) {
  const providerConfig = readCuratedProvider();
  if (!providerConfig) return;
  const curated = providerConfig.models as ProviderModelConfig[];

  const apiKey = resolveApiKey();
  const observed = await discoverLiveModels(apiKey);
  const known = new Set(curated.map(model => model.id));
  const added = observed
    .map(toModelConfig)
    .filter((model): model is ProviderModelConfig => model !== null && !known.has(model.id));
  if (added.length === 0) return;

  const { models: _omitted, ...restProvider } = providerConfig;

  pi.registerProvider(PROVIDER, {
    ...restProvider,
    baseUrl: restProvider.baseUrl || BASE_URL,
    apiKey: apiKey || restProvider.apiKey || "$COMMANDCODE_API_KEY",
    api: restProvider.api || "openai-completions",
    models: [...curated, ...added],
  });
}
