// @ts-nocheck — pi extension runs via jiti
// Antigravity (AGY) Bridge Provider Extension for Pi Coding Agent
// Connects Pi to the local Antigravity subscription proxy on 127.0.0.1:8085
// Unlocks Claude Sonnet 4.6, Claude Opus 4.6 (Thinking), and Gemini 3.7 Flash

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const BASE_URL = "http://127.0.0.1:58285/v1";

const MODELS = [
  {
    id: "gemini-3.7-flash-high",
    name: "Gemini 3.7 Flash High (Antigravity)",
    reasoning: true,
    input: ["text" as const],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 2000000,
    maxTokens: 65536,
    compat: {
      supportsDeveloperRole: false,
      supportsReasoningEffort: false,
      maxTokensField: "max_tokens" as const,
      supportsUsageInStreaming: true,
      supportsFinishReason: true,
      sendSessionAffinityHeaders: true,
      thinkingFormat: "deepseek" as const,
    },
  },
  {
    id: "gemini-3.7-flash-medium",
    name: "Gemini 3.7 Flash Medium (Antigravity)",
    reasoning: true,
    input: ["text" as const],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 2000000,
    maxTokens: 65536,
    compat: {
      supportsDeveloperRole: false,
      supportsReasoningEffort: false,
      maxTokensField: "max_tokens" as const,
      supportsUsageInStreaming: true,
      supportsFinishReason: true,
      sendSessionAffinityHeaders: true,
      thinkingFormat: "deepseek" as const,
    },
  },
  {
    id: "claude-sonnet-4-6",
    name: "Claude Sonnet 4.6 Thinking (Antigravity)",
    reasoning: true,
    input: ["text" as const],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 200000,
    maxTokens: 64000,
    compat: {
      supportsDeveloperRole: false,
      supportsReasoningEffort: false,
      maxTokensField: "max_tokens" as const,
      supportsUsageInStreaming: true,
      supportsFinishReason: true,
      sendSessionAffinityHeaders: true,
      thinkingFormat: "deepseek" as const,
    },
  },
  {
    id: "claude-opus-4-6-thinking",
    name: "Claude Opus 4.6 Thinking (Antigravity)",
    reasoning: true,
    input: ["text" as const],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 200000,
    maxTokens: 64000,
    compat: {
      supportsDeveloperRole: false,
      supportsReasoningEffort: false,
      maxTokensField: "max_tokens" as const,
      supportsUsageInStreaming: true,
      supportsFinishReason: true,
      sendSessionAffinityHeaders: true,
      thinkingFormat: "deepseek" as const,
    },
  },
  {
    id: "gemini-3.1-pro-high",
    name: "Gemini 3.1 Pro High (Antigravity)",
    reasoning: true,
    input: ["text" as const],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 1000000,
    maxTokens: 32768,
    compat: {
      supportsDeveloperRole: false,
      supportsReasoningEffort: false,
      maxTokensField: "max_tokens" as const,
      supportsUsageInStreaming: true,
      supportsFinishReason: true,
      sendSessionAffinityHeaders: true,
      thinkingFormat: "deepseek" as const,
    },
  },
  {
    id: "gpt-oss-120b-medium",
    name: "GPT-OSS 120B (Antigravity)",
    reasoning: true,
    input: ["text" as const],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 128000,
    maxTokens: 16384,
    compat: {
      supportsDeveloperRole: false,
      supportsReasoningEffort: false,
      maxTokensField: "max_tokens" as const,
      supportsUsageInStreaming: true,
      supportsFinishReason: true,
      sendSessionAffinityHeaders: true,
      thinkingFormat: "deepseek" as const,
    },
  },
];

export default async function (pi: ExtensionAPI) {
  pi.registerProvider("antigravity", {
    name: "Antigravity Bridge",
    baseUrl: BASE_URL,
    apiKey: "antigravity-local-token",
    authHeader: false,
    api: "openai-completions",
    models: MODELS,
  });
}
