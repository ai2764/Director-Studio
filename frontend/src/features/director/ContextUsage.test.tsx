// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { ContextUsagePanel } from "./ContextUsage";
import type { ContextUsage } from "./api";

afterEach(cleanup);

const usage: ContextUsage = {
  call_id: "call-1", sequence: 1, purpose: "turn", provider: "ollama", model: "qwen",
  status: "running", context_window: 32768, output_limit: 4096, input_budget: 28672,
  capacity_source: "provider_reported",
  estimated_input_tokens: 22000, estimated_parts: { system: 8000, conversation: 10000, tools: 4000, format: 0 },
  image_count: 2, input_tokens: null, output_tokens: null, reasoning_tokens: null,
  thinking_chars: null, content_chars: null, tool_calls: null, finish_reason: null, elapsed_ms: 0,
};

it("labels estimates and missing image/usage counts while inference is running", () => {
  render(<ContextUsagePanel calls={[usage]}><button>Compact context</button></ContextUsagePanel>);
  expect(screen.queryByText(/excludes image tokens/i)).toBeNull();
  expect(screen.queryByRole("button", { name: "Compact context" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /Context · ~22,000/ }));
  expect(screen.getByRole("dialog", { name: "Context details" })).toBeTruthy();
  expect(screen.getAllByText(/~22,000/).length).toBeGreaterThan(0);
  expect(screen.getByText(/excludes image tokens/i)).toBeTruthy();
  expect(screen.getByText(/actual counts arrive when/i)).toBeTruthy();
  expect(screen.getByText(/32,768 · provider reported/i)).toBeTruthy();
  expect(screen.getByRole("button", { name: "Compact context" })).toBeTruthy();
  expect(screen.queryByText(/Output truncated/)).toBeNull();
});

it("closes the context dialog with Escape and returns focus to its trigger", () => {
  render(<ContextUsagePanel calls={[usage]} />);
  const trigger = screen.getByRole("button", { name: /Context · ~22,000/ });
  fireEvent.click(trigger);
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(trigger);
});

it("distinguishes output truncation from context overflow using actual counts", () => {
  render(<ContextUsagePanel calls={[{ ...usage, status: "output_truncated", input_tokens: 28939,
    output_tokens: 4096, thinking_chars: 17000, content_chars: 0, tool_calls: 0, finish_reason: "length" }]} />);
  fireEvent.click(screen.getByRole("button", { name: /Context · 28,939/ }));
  expect(screen.getAllByText(/Output truncated/).length).toBeGreaterThan(0);
  expect(screen.getAllByText(/28,939/).length).toBeGreaterThan(0);
  expect(screen.getAllByText(/4,096 \/ 4,096/).length).toBeGreaterThan(0);
  expect(screen.getByText(/17,000 chars/)).toBeTruthy();
  expect(screen.queryByText("Context overflow")).toBeNull();
});

it("shows unknown provider capacity without a fabricated percentage", () => {
  render(<ContextUsagePanel calls={[{ ...usage, context_window: null, input_budget: null, output_limit: null,
    status: "completed", input_tokens: 100, output_tokens: 20 }]} />);
  fireEvent.click(screen.getByRole("button", { name: /Context · 100/ }));
  expect(screen.getByText(/capacity not reported/i)).toBeTruthy();
  expect(screen.queryByRole("meter")).toBeNull();
});

it("labels a configured fallback instead of presenting it as provider reported", () => {
  render(<ContextUsagePanel calls={[{ ...usage, capacity_source: "configured_fallback" }]} />);
  fireEvent.click(screen.getByRole("button", { name: /Context · ~22,000/ }));
  expect(screen.getByText(/32,768 · configured fallback/i)).toBeTruthy();
});

it("uses a compact percentage label while keeping full context details accessible", () => {
  render(<ContextUsagePanel calls={[usage]} compact />);
  const trigger = screen.getByRole("button", { name: /Context · ~22,000 \/ 32,768/ });
  expect(trigger.textContent).toContain("Context 67%");
  expect(trigger.textContent).not.toContain("Waiting for model");
  fireEvent.click(trigger);
  expect(screen.getByRole("dialog", { name: "Context details" })).toBeTruthy();
});
