import {
  CompletionContext,
  type CompletionResult,
} from "@codemirror/autocomplete";
import { EditorState } from "@codemirror/state";
import { describe, expect, it } from "vitest";
import { rCompletionSource } from "../languages/r-completions";

// rCompletionSource is synchronous, so we can safely cast
function getExplicitCompletions(
  doc: string,
  pos?: number,
): CompletionResult | null {
  const state = EditorState.create({ doc });
  const ctx = new CompletionContext(state, pos ?? doc.length, true);
  return rCompletionSource(ctx) as CompletionResult | null;
}

describe("rCompletionSource", () => {
  describe("basic completions", () => {
    it("returns completions for a partial keyword", () => {
      const result = getExplicitCompletions("func");
      expect(result).not.toBeNull();
      const labels = result!.options.map((o) => o.label);
      expect(labels).toContain("function");
    });

    it("returns completions for base R functions", () => {
      const result = getExplicitCompletions("mea");
      expect(result).not.toBeNull();
      const labels = result!.options.map((o) => o.label);
      expect(labels).toContain("mean");
      expect(labels).toContain("median");
    });

    it("returns completions for constants", () => {
      const result = getExplicitCompletions("TRU");
      expect(result).not.toBeNull();
      const labels = result!.options.map((o) => o.label);
      expect(labels).toContain("TRUE");
    });

    it("returns completions for dplyr functions", () => {
      const result = getExplicitCompletions("filt");
      expect(result).not.toBeNull();
      const labels = result!.options.map((o) => o.label);
      expect(labels).toContain("filter");
    });

    it("returns completions for ggplot2 functions", () => {
      const result = getExplicitCompletions("geom_po");
      expect(result).not.toBeNull();
      const labels = result!.options.map((o) => o.label);
      expect(labels).toContain("geom_point");
    });

    it("returns completions for stringr functions", () => {
      const result = getExplicitCompletions("str_de");
      expect(result).not.toBeNull();
      const labels = result!.options.map((o) => o.label);
      expect(labels).toContain("str_detect");
    });

    it("returns completions for purrr functions", () => {
      const result = getExplicitCompletions("map_c");
      expect(result).not.toBeNull();
      const labels = result!.options.map((o) => o.label);
      expect(labels).toContain("map_chr");
    });

    it("returns completions for readr functions", () => {
      const result = getExplicitCompletions("read_c");
      expect(result).not.toBeNull();
      const labels = result!.options.map((o) => o.label);
      expect(labels).toContain("read_csv");
    });

    it("returns completions for tidyr functions", () => {
      const result = getExplicitCompletions("pivot_l");
      expect(result).not.toBeNull();
      const labels = result!.options.map((o) => o.label);
      expect(labels).toContain("pivot_longer");
    });
  });

  describe("string and comment suppression", () => {
    it("suppresses completions inside double-quoted strings", () => {
      const result = getExplicitCompletions('x <- "mea');
      expect(result).toBeNull();
    });

    it("suppresses completions inside single-quoted strings", () => {
      const result = getExplicitCompletions("x <- 'mea");
      expect(result).toBeNull();
    });

    it("suppresses completions inside comments", () => {
      const result = getExplicitCompletions("# mea");
      expect(result).toBeNull();
    });

    it("suppresses completions in mid-line comments", () => {
      const result = getExplicitCompletions("x <- 1 # mea");
      expect(result).toBeNull();
    });

    it("does not suppress after a closed string", () => {
      const result = getExplicitCompletions('"hello" mea');
      expect(result).not.toBeNull();
      const labels = result!.options.map((o) => o.label);
      expect(labels).toContain("mean");
    });

    it("does not suppress on a new line after a comment", () => {
      const result = getExplicitCompletions("# comment\nmea");
      expect(result).not.toBeNull();
      const labels = result!.options.map((o) => o.label);
      expect(labels).toContain("mean");
    });
  });

  describe("snippets", () => {
    it("includes function snippet", () => {
      const result = getExplicitCompletions("func");
      expect(result).not.toBeNull();
      const options = result!.options;
      const funcSnippet = options.find(
        (o) => o.label === "function" && o.type === "keyword",
      );
      expect(funcSnippet).toBeDefined();
    });

    it("includes for loop snippet", () => {
      const result = getExplicitCompletions("for");
      expect(result).not.toBeNull();
      const options = result!.options;
      const forSnippet = options.find(
        (o) => o.label === "for" && o.type === "keyword",
      );
      expect(forSnippet).toBeDefined();
    });

    it("includes tryCatch snippet", () => {
      const result = getExplicitCompletions("tryC");
      expect(result).not.toBeNull();
      const labels = result!.options.map((o) => o.label);
      expect(labels).toContain("tryCatch");
    });
  });

  describe("detail annotations", () => {
    it("dplyr functions have detail annotation", () => {
      const result = getExplicitCompletions("select");
      expect(result).not.toBeNull();
      const selectOption = result!.options.find((o) => o.label === "select");
      expect(selectOption).toBeDefined();
      expect(selectOption!.detail).toBe("dplyr");
    });

    it("ggplot2 functions have detail annotation", () => {
      const result = getExplicitCompletions("ggplot");
      expect(result).not.toBeNull();
      const ggplotOption = result!.options.find((o) => o.label === "ggplot");
      expect(ggplotOption).toBeDefined();
      expect(ggplotOption!.detail).toBe("ggplot2");
    });

    it("base R functions have no detail annotation", () => {
      const result = getExplicitCompletions("mean");
      expect(result).not.toBeNull();
      const meanOption = result!.options.find((o) => o.label === "mean");
      expect(meanOption).toBeDefined();
      expect(meanOption!.detail).toBeUndefined();
    });
  });
});
