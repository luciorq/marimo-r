import { describe, expect, it } from "vitest";
import type { CompletionConfig } from "@/core/config/config-schema";
import { HotkeyProvider } from "@/core/hotkeys/hotkeys";
import {
  DEFAULT_R_OUTPUT,
  DEFAULT_R_PLOT_DPI,
  DEFAULT_R_PLOT_FORMAT,
  DEFAULT_R_PLOT_HEIGHT,
  DEFAULT_R_PLOT_WIDTH,
  RLanguageAdapter,
} from "../languages/r";

const adapter = new RLanguageAdapter();

describe("RLanguageAdapter", () => {
  it("default metadata includes output flags and plot settings", () => {
    expect(adapter.defaultMetadata).toEqual({
      prefix: "",
      suffix: "",
      outputVar: DEFAULT_R_OUTPUT,
      showOutput: true,
      capture: true,
      plot: true,
      inputs: "",
      plotFormat: DEFAULT_R_PLOT_FORMAT,
      plotWidth: DEFAULT_R_PLOT_WIDTH,
      plotHeight: DEFAULT_R_PLOT_HEIGHT,
      plotDpi: DEFAULT_R_PLOT_DPI,
    });
  });

  it("transformIn parses output variable and flags", () => {
    const code =
      '_r_output = mo.r("""\nx <- 1\n""", output=False, capture=False, plot=False)';
    const [inner, offset, metadata] = adapter.transformIn(code);
    expect(inner).toBe("x <- 1");
    expect(offset).toBe(0);
    expect(metadata.outputVar).toBe("_r_output");
    expect(metadata.showOutput).toBe(false);
    expect(metadata.capture).toBe(false);
    expect(metadata.plot).toBe(false);
  });

  it("transformIn parses old inline format (backward compat)", () => {
    const code =
      '_r_output = mo.r("""x <- 1""", output=False, capture=False, plot=False)';
    const [inner, offset, metadata] = adapter.transformIn(code);
    expect(inner).toBe("x <- 1");
    expect(offset).toBe(0);
    expect(metadata.outputVar).toBe("_r_output");
    expect(metadata.showOutput).toBe(false);
    expect(metadata.capture).toBe(false);
    expect(metadata.plot).toBe(false);
  });

  it("transformOut emits flags only when disabled", () => {
    const [pythonCode] = adapter.transformOut("x <- 1", {
      prefix: "",
      suffix: "",
      outputVar: "result",
      showOutput: false,
      capture: true,
      plot: false,
      inputs: "",
      plotFormat: DEFAULT_R_PLOT_FORMAT,
      plotWidth: DEFAULT_R_PLOT_WIDTH,
      plotHeight: DEFAULT_R_PLOT_HEIGHT,
      plotDpi: DEFAULT_R_PLOT_DPI,
    });
    expect(pythonCode).toBe(
      'result = mo.r("""\nx <- 1\n""", output=False, plot=False)',
    );
  });

  describe("quote escaping", () => {
    it("handles R code that is a quoted string", () => {
      const rCode = '"TRUE"';
      const [pythonCode] = adapter.transformOut(rCode, adapter.defaultMetadata);
      expect(pythonCode).toBe('_r_output = mo.r("""\n"TRUE"\n""")');
      // Verify round-trip
      const [inner] = adapter.transformIn(pythonCode);
      expect(inner).toBe(rCode);
    });

    it("handles R code ending with a double quote", () => {
      const rCode = 'x <- "hello"';
      const [pythonCode] = adapter.transformOut(rCode, adapter.defaultMetadata);
      expect(pythonCode).toBe('_r_output = mo.r("""\nx <- "hello"\n""")');
      const [inner] = adapter.transformIn(pythonCode);
      expect(inner).toBe(rCode);
    });

    it("handles R code starting with a double quote", () => {
      const rCode = '"A" -> x';
      const [pythonCode] = adapter.transformOut(rCode, adapter.defaultMetadata);
      expect(pythonCode).toBe('_r_output = mo.r("""\n"A" -> x\n""")');
      const [inner] = adapter.transformIn(pythonCode);
      expect(inner).toBe(rCode);
    });

    it("escapes triple-quote sequences in R code", () => {
      const rCode = 'x <- """test"""';
      const [pythonCode] = adapter.transformOut(rCode, adapter.defaultMetadata);
      expect(pythonCode).toBe(
        '_r_output = mo.r("""\nx <- \\"""test\\"""\n""")',
      );
      const [inner] = adapter.transformIn(pythonCode);
      expect(inner).toBe(rCode);
    });

    it("round-trips multiline R code with quotes", () => {
      const rCode = 'x <- "hello"\nprint(x)\ny <- "world"';
      const [pythonCode] = adapter.transformOut(rCode, adapter.defaultMetadata);
      const [inner] = adapter.transformIn(pythonCode);
      expect(inner).toBe(rCode);
    });

    it("round-trips simple R code without quotes", () => {
      const rCode = "x <- 1 + 2";
      const [pythonCode] = adapter.transformOut(rCode, adapter.defaultMetadata);
      const [inner] = adapter.transformIn(pythonCode);
      expect(inner).toBe(rCode);
    });

    it("round-trips R code with metadata and quotes", () => {
      const rCode = '"TRUE"';
      const metadata = {
        prefix: "",
        suffix: "",
        outputVar: "result",
        showOutput: false,
        capture: true,
        plot: false,
        inputs: "",
        plotFormat: DEFAULT_R_PLOT_FORMAT,
        plotWidth: DEFAULT_R_PLOT_WIDTH,
        plotHeight: DEFAULT_R_PLOT_HEIGHT,
        plotDpi: DEFAULT_R_PLOT_DPI,
      };
      const [pythonCode] = adapter.transformOut(rCode, metadata);
      expect(pythonCode).toBe(
        'result = mo.r("""\n"TRUE"\n""", output=False, plot=False)',
      );
      const [inner, , parsedMeta] = adapter.transformIn(pythonCode);
      expect(inner).toBe(rCode);
      expect(parsedMeta.outputVar).toBe("result");
      expect(parsedMeta.showOutput).toBe(false);
      expect(parsedMeta.plot).toBe(false);
    });
  });

  describe("isSupported", () => {
    it("detects new newline-padded format", () => {
      expect(adapter.isSupported('_r_output = mo.r("""\n"TRUE"\n""")')).toBe(
        true,
      );
    });

    it("detects old inline format", () => {
      expect(adapter.isSupported('_r_output = mo.r("""x <- 1""")')).toBe(true);
    });

    it("rejects non-R code", () => {
      expect(adapter.isSupported("x = 1")).toBe(false);
    });
  });

  describe("boolean arg stripping with other kwargs", () => {
    it("round-trips plot=False with inline inputs", () => {
      const code = 'result = mo.r("""\ncode\n""", inputs={"x": 1}, plot=False)';
      const [rCode, , meta] = adapter.transformIn(code);
      expect(rCode).toBe("code");
      expect(meta.plot).toBe(false);
      expect(meta.inputs).toBe('{"x": 1}');

      const [output] = adapter.transformOut(rCode, meta);
      expect(adapter.isSupported(output)).toBe(true);
      // No double commas
      expect(output).not.toMatch(/,\s*,/);

      const [rCode2, , meta2] = adapter.transformIn(output);
      expect(rCode2).toBe(rCode);
      expect(meta2.plot).toBe(false);
      expect(meta2.inputs).toBe('{"x": 1}');
    });

    it("round-trips plot=False in multiline format with trailing commas", () => {
      const code = [
        "_r_output = mo.r(",
        '        """',
        "    set.seed(42)",
        '    """,',
        '        inputs={"data": df},',
        "        plot=False,",
        "    )",
      ].join("\n");

      const [rCode, , meta] = adapter.transformIn(code);
      expect(meta.plot).toBe(false);
      expect(meta.inputs).toBe('{"data": df}');

      const [output] = adapter.transformOut(rCode, meta);
      expect(adapter.isSupported(output)).toBe(true);
      expect(output).not.toMatch(/,\s*,/);

      const [rCode2, , meta2] = adapter.transformIn(output);
      expect(rCode2).toBe(rCode);
      expect(meta2.plot).toBe(false);
      expect(meta2.inputs).toBe('{"data": df}');
    });

    it("round-trips output=False and plot=False with inputs", () => {
      const code = [
        "_r_output = mo.r(",
        '        """',
        "    set.seed(42)",
        '    """,',
        '        inputs={"data": df},',
        "        output=False,",
        "        plot=False,",
        "    )",
      ].join("\n");

      const [rCode, , meta] = adapter.transformIn(code);
      expect(meta.showOutput).toBe(false);
      expect(meta.plot).toBe(false);
      expect(meta.inputs).toBe('{"data": df}');

      const [output] = adapter.transformOut(rCode, meta);
      expect(adapter.isSupported(output)).toBe(true);
      expect(output).not.toMatch(/,\s*,/);

      const [rCode2, , meta2] = adapter.transformIn(output);
      expect(rCode2).toBe(rCode);
      expect(meta2.showOutput).toBe(false);
      expect(meta2.plot).toBe(false);
      expect(meta2.inputs).toBe('{"data": df}');
    });

    it("preserves plot_format in metadata", () => {
      const code =
        '_r_output = mo.r("""\ncode\n""", plot_format="svg", plot=False)';
      const [rCode, , meta] = adapter.transformIn(code);
      expect(rCode).toBe("code");
      expect(meta.plot).toBe(false);
      expect(meta.plotFormat).toBe("svg");

      const [output] = adapter.transformOut(rCode, meta);
      expect(output).toContain('plot_format="svg"');
      expect(output).toContain("plot=False");
      expect(adapter.isSupported(output)).toBe(true);
    });

    it("round-trips all booleans false with no other kwargs", () => {
      const code =
        '_r_output = mo.r("""\nx <- 1\n""", output=False, capture=False, plot=False)';
      const [rCode, , meta] = adapter.transformIn(code);
      expect(rCode).toBe("x <- 1");
      expect(meta.showOutput).toBe(false);
      expect(meta.capture).toBe(false);
      expect(meta.plot).toBe(false);
      expect(meta.suffix).toBe("");

      const [output] = adapter.transformOut(rCode, meta);
      expect(output).toBe(code);
    });

    it("round-trips plot=False only with no other kwargs", () => {
      const code = '_r_output = mo.r("""\nx <- 1\n""", plot=False)';
      const [rCode, , meta] = adapter.transformIn(code);
      expect(meta.plot).toBe(false);
      expect(meta.suffix).toBe("");

      const [output] = adapter.transformOut(rCode, meta);
      expect(output).toBe(code);
    });
  });

  describe("inputs kwarg", () => {
    it("parses inputs from inline code", () => {
      const code = '_r_output = mo.r("""\nx <- 1\n""", inputs={"x": df})';
      const [rCode, , meta] = adapter.transformIn(code);
      expect(rCode).toBe("x <- 1");
      expect(meta.inputs).toBe('{"x": df}');
    });

    it("emits inputs kwarg when non-empty", () => {
      const [pythonCode] = adapter.transformOut("x <- 1", {
        ...adapter.defaultMetadata,
        inputs: '{"x": df}',
      });
      expect(pythonCode).toBe(
        '_r_output = mo.r("""\nx <- 1\n""", inputs={"x": df})',
      );
    });

    it("omits inputs kwarg when empty", () => {
      const [pythonCode] = adapter.transformOut("x <- 1", {
        ...adapter.defaultMetadata,
        inputs: "",
      });
      expect(pythonCode).toBe('_r_output = mo.r("""\nx <- 1\n""")');
    });

    it("round-trips inputs with nested braces", () => {
      const code = '_r_output = mo.r("""\nx <- 1\n""", inputs={"x": {"a": 1}})';
      const [rCode, , meta] = adapter.transformIn(code);
      expect(rCode).toBe("x <- 1");
      expect(meta.inputs).toBe('{"x": {"a": 1}}');

      const [output] = adapter.transformOut(rCode, meta);
      expect(output).toContain('inputs={"x": {"a": 1}}');
      const [, , meta2] = adapter.transformIn(output);
      expect(meta2.inputs).toBe('{"x": {"a": 1}}');
    });

    it("round-trips inputs with boolean flags", () => {
      const code =
        '_r_output = mo.r("""\ncode\n""", inputs={"df": my_df}, output=False, plot=False)';
      const [rCode, , meta] = adapter.transformIn(code);
      expect(rCode).toBe("code");
      expect(meta.inputs).toBe('{"df": my_df}');
      expect(meta.showOutput).toBe(false);
      expect(meta.plot).toBe(false);

      const [output] = adapter.transformOut(rCode, meta);
      expect(output).toContain('inputs={"df": my_df}');
      expect(output).toContain("output=False");
      expect(output).toContain("plot=False");
      expect(output).not.toMatch(/,\s*,/);
    });
  });

  describe("plot settings kwargs", () => {
    it("parses all plot settings from code", () => {
      const code =
        '_r_output = mo.r("""\ncode\n""", plot_format="svg", plot_width=800, plot_height=600, plot_dpi=150)';
      const [rCode, , meta] = adapter.transformIn(code);
      expect(rCode).toBe("code");
      expect(meta.plotFormat).toBe("svg");
      expect(meta.plotWidth).toBe(800);
      expect(meta.plotHeight).toBe(600);
      expect(meta.plotDpi).toBe(150);
    });

    it("emits plot settings only when non-default", () => {
      const [pythonCode] = adapter.transformOut("x <- 1", {
        ...adapter.defaultMetadata,
        plotFormat: "svg",
        plotWidth: 800,
        plotHeight: 600,
        plotDpi: 150,
      });
      expect(pythonCode).toBe(
        '_r_output = mo.r("""\nx <- 1\n""", plot_format="svg", plot_width=800, plot_height=600, plot_dpi=150)',
      );
    });

    it("omits plot settings when at defaults", () => {
      const [pythonCode] = adapter.transformOut("x <- 1", {
        ...adapter.defaultMetadata,
      });
      expect(pythonCode).toBe('_r_output = mo.r("""\nx <- 1\n""")');
    });

    it("round-trips plot settings", () => {
      const code =
        '_r_output = mo.r("""\ncode\n""", plot_format="svg", plot_width=800, plot_height=600, plot_dpi=150)';
      const [rCode, , meta] = adapter.transformIn(code);
      const [output] = adapter.transformOut(rCode, meta);
      const [rCode2, , meta2] = adapter.transformIn(output);
      expect(rCode2).toBe(rCode);
      expect(meta2.plotFormat).toBe("svg");
      expect(meta2.plotWidth).toBe(800);
      expect(meta2.plotHeight).toBe(600);
      expect(meta2.plotDpi).toBe(150);
    });

    it("round-trips partial plot settings (only format changed)", () => {
      const [pythonCode] = adapter.transformOut("x <- 1", {
        ...adapter.defaultMetadata,
        plotFormat: "svg",
      });
      expect(pythonCode).toContain('plot_format="svg"');
      expect(pythonCode).not.toContain("plot_width");
      expect(pythonCode).not.toContain("plot_height");
      expect(pythonCode).not.toContain("plot_dpi");

      const [, , meta] = adapter.transformIn(pythonCode);
      expect(meta.plotFormat).toBe("svg");
      expect(meta.plotWidth).toBe(DEFAULT_R_PLOT_WIDTH);
      expect(meta.plotHeight).toBe(DEFAULT_R_PLOT_HEIGHT);
      expect(meta.plotDpi).toBe(DEFAULT_R_PLOT_DPI);
    });
  });

  describe("combined kwargs round-trip", () => {
    it("round-trips all kwargs together", () => {
      const [pythonCode] = adapter.transformOut("library(ggplot2)", {
        ...adapter.defaultMetadata,
        outputVar: "result",
        showOutput: false,
        capture: false,
        plot: false,
        inputs: '{"df": my_df, "n": 10}',
        plotFormat: "svg",
        plotWidth: 1200,
        plotHeight: 800,
        plotDpi: 200,
      });
      expect(pythonCode).toContain("result = mo.r(");
      expect(pythonCode).toContain('inputs={"df": my_df, "n": 10}');
      expect(pythonCode).toContain("output=False");
      expect(pythonCode).toContain("capture=False");
      expect(pythonCode).toContain("plot=False");
      expect(pythonCode).toContain('plot_format="svg"');
      expect(pythonCode).toContain("plot_width=1200");
      expect(pythonCode).toContain("plot_height=800");
      expect(pythonCode).toContain("plot_dpi=200");
      expect(pythonCode).not.toMatch(/,\s*,/);

      const [rCode, , meta] = adapter.transformIn(pythonCode);
      expect(rCode).toBe("library(ggplot2)");
      expect(meta.outputVar).toBe("result");
      expect(meta.showOutput).toBe(false);
      expect(meta.capture).toBe(false);
      expect(meta.plot).toBe(false);
      expect(meta.inputs).toBe('{"df": my_df, "n": 10}');
      expect(meta.plotFormat).toBe("svg");
      expect(meta.plotWidth).toBe(1200);
      expect(meta.plotHeight).toBe(800);
      expect(meta.plotDpi).toBe(200);
    });
  });

  describe("getExtension", () => {
    const baseCompletionConfig: CompletionConfig = {
      activate_on_typing: true,
      copilot: false,
      signature_hint_on_typing: false,
    };
    const baseHotkeys: HotkeyProvider = HotkeyProvider.create();

    it("returns extensions for non-LSP mode with syntax highlighting and fallback completions", () => {
      const extensions = adapter.getExtension(
        "cell_id" as any,
        baseCompletionConfig,
        baseHotkeys as any,
        "code" as any,
        {},
      );
      expect(Array.isArray(extensions)).toBe(true);
      expect(extensions.length).toBeGreaterThanOrEqual(2);
      // Each element should be a valid CodeMirror Extension
      extensions.forEach((ext) => {
        // Extensions can be arrays, functions, or objects
        expect(ext).toBeDefined();
      });
    });

    it("supports LSP-enabled extensions when r config has enabled: true", () => {
      // Note: hasCapability("r_lsp") returns false in test env
      // so this should return non-LSP path even with r enabled
      const extensions = adapter.getExtension(
        "cell_id" as any,
        baseCompletionConfig,
        baseHotkeys as any,
        "code" as any,
        { r: { enabled: true, backend: "languageserver" } },
      );
      expect(Array.isArray(extensions)).toBe(true);
      expect(extensions.length).toBeGreaterThanOrEqual(2);
    });
  });
});
