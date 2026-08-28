import { autocompletion } from "@codemirror/autocomplete";
import { LanguageSupport, StreamLanguage } from "@codemirror/language";
import { r } from "@codemirror/legacy-modes/mode/r";
import type { Extension } from "@codemirror/state";
import {
  documentUri,
  LanguageServerClient,
  languageServerWithClient,
} from "@marimo-team/codemirror-languageserver";
import type { CellId } from "@/core/cells/ids";
import { hasCapability } from "@/core/config/capabilities";
import type {
  CompletionConfig,
  DiagnosticsConfig,
  LSPConfig,
} from "@/core/config/config-schema";
import type { HotkeyProvider } from "@/core/hotkeys/hotkeys";
import { getRequestClient } from "@/core/network/requests";
import { Logger } from "@/utils/Logger";
import { once } from "@/utils/once";
import { completionKeymap } from "../../completion/keymap";
import type { PlaceholderType } from "../../config/types";
import { FederatedLanguageServerClient } from "../../lsp/federated-lsp";
import { NotebookLanguageServerClient } from "../../lsp/notebook-lsp";
import { createTransport } from "../../lsp/transports";
import { CellDocumentUri } from "../../lsp/types";
import { getLspRootUri } from "../../lsp/utils";
import type { LanguageAdapter } from "../types";
import { rCompletionSource } from "./r-completions";

const R_WRAPPER_REGEX =
  /(?<outputVar>[A-Za-z_]\w*)?\s*(?:=\s*)?(?:mo|marimo)\.r\((?<prefix>[\s\S]*?)(?<stringPrefix>[rf]?)"""(?<code>(?:(?!(?<!\\)""")[\s\S])*)"""\s*(?<suffix>[\s\S]*?)\)/m;

export const DEFAULT_R_OUTPUT = "_r_output";
const DEFAULT_R_SHOW_OUTPUT = true;
const DEFAULT_R_CAPTURE = true;
const DEFAULT_R_PLOT = true;
export const DEFAULT_R_PLOT_FORMAT = "png";
export const DEFAULT_R_PLOT_WIDTH = 960;
export const DEFAULT_R_PLOT_HEIGHT = 640;
export const DEFAULT_R_PLOT_DPI = 120;

export interface RLanguageMetadata {
  prefix: string;
  suffix: string;
  outputVar: string;
  showOutput: boolean;
  capture: boolean;
  plot: boolean;
  inputs: string;
  plotFormat: string;
  plotWidth: number;
  plotHeight: number;
  plotDpi: number;
}

const parseBooleanArg = (
  args: string,
  name: "output" | "capture" | "plot",
): boolean | undefined => {
  const regex = new RegExp(`\\b${name}\\s*=\\s*(True|False)\\b`, "g");
  let match = regex.exec(args);
  let result: boolean | undefined;
  while (match) {
    result = match[1] === "True";
    match = regex.exec(args);
  }
  return result;
};

const stripBooleanArg = (
  segment: string,
  name: "output" | "capture" | "plot",
): string => {
  // Remove the boolean kwarg along with its surrounding comma/whitespace.
  // We try two patterns:
  //   1) A leading comma before the arg:  ", plot=False"
  //   2) A trailing comma after the arg:  "plot=False, "  (when it's the first arg)
  // After removal we also clean up any resulting artifacts.
  let result = segment.replace(
    new RegExp(
      `\\s*,\\s*${name}\\s*=\\s*(?:True|False)` + // ", name=Value"
        `|${name}\\s*=\\s*(?:True|False)\\s*,?`, // "name=Value," (first-arg case)
      "g",
    ),
    "",
  );
  // Collapse any double/triple commas left behind
  result = result.replace(/,(\s*,)+/g, ",");
  // If only whitespace (or a lone comma) remains, collapse to empty
  if (result.trim() === "" || result.trim() === ",") {
    return "";
  }
  // Remove trailing comma followed by only whitespace at end of segment.
  // This prevents dangling commas when a boolean was the last arg
  // (e.g. suffix ",\n    inputs={...},\n    " -> should end without trailing comma).
  result = result.replace(/,\s*$/, "");
  return result;
};

/**
 * Parse a string kwarg like `plot_format="svg"` from the combined args string.
 */
const parseStringArg = (args: string, name: string): string | undefined => {
  const regex = new RegExp(`\\b${name}\\s*=\\s*"([^"]*)"`, "g");
  let match = regex.exec(args);
  let result: string | undefined;
  while (match) {
    result = match[1];
    match = regex.exec(args);
  }
  return result;
};

/**
 * Parse an integer kwarg like `plot_width=960` from the combined args string.
 */
const parseIntArg = (args: string, name: string): number | undefined => {
  const regex = new RegExp(`\\b${name}\\s*=\\s*(\\d+)`, "g");
  let match = regex.exec(args);
  let result: number | undefined;
  while (match) {
    result = Number.parseInt(match[1], 10);
    match = regex.exec(args);
  }
  return result;
};

/**
 * Parse the `inputs={...}` dict expression from the combined args string.
 * Handles nested braces so `inputs={"x": {"a": 1}}` is captured correctly.
 */
const parseInputsArg = (args: string): string | undefined => {
  const startRegex = /\binputs\s*=\s*\{/g;
  const startMatch = startRegex.exec(args);
  if (!startMatch) {
    return undefined;
  }
  // Find the matching closing brace
  const openIdx = startMatch.index + startMatch[0].length - 1;
  let depth = 0;
  for (let i = openIdx; i < args.length; i++) {
    if (args[i] === "{") {
      depth++;
    } else if (args[i] === "}") {
      depth--;
    }
    if (depth === 0) {
      return args.slice(openIdx, i + 1);
    }
  }
  return undefined;
};

/**
 * Strip a simple kwarg (string or integer valued) from a segment.
 * E.g. stripSimpleArg(segment, "plot_format", /"[^"]*"/) removes `plot_format="png"`.
 */
const stripSimpleArg = (
  segment: string,
  name: string,
  valuePattern: string,
): string => {
  let result = segment.replace(
    new RegExp(
      `\\s*,\\s*${name}\\s*=\\s*${valuePattern}` +
        `|${name}\\s*=\\s*${valuePattern}\\s*,?`,
      "g",
    ),
    "",
  );
  result = result.replace(/,(\s*,)+/g, ",");
  if (result.trim() === "" || result.trim() === ",") {
    return "";
  }
  result = result.replace(/,\s*$/, "");
  return result;
};

/**
 * Strip the `inputs={...}` kwarg from a segment, handling nested braces.
 */
const stripInputsArg = (segment: string): string => {
  const startRegex = /(?:\s*,\s*)?inputs\s*=\s*\{/g;
  const startMatch = startRegex.exec(segment);
  if (!startMatch) {
    // Try the other pattern: inputs={...},
    const altRegex = /inputs\s*=\s*\{/g;
    const altMatch = altRegex.exec(segment);
    if (!altMatch) {
      return segment;
    }
    const openIdx = altMatch.index + altMatch[0].length - 1;
    let depth = 0;
    for (let i = openIdx; i < segment.length; i++) {
      if (segment[i] === "{") {
        depth++;
      } else if (segment[i] === "}") {
        depth--;
      }
      if (depth === 0) {
        // Remove trailing comma/whitespace
        let endIdx = i + 1;
        const trailing = segment.slice(endIdx);
        const trailingMatch = trailing.match(/^\s*,?\s*/);
        if (trailingMatch) {
          endIdx += trailingMatch[0].length;
        }
        const result = segment.slice(0, altMatch.index) + segment.slice(endIdx);
        if (result.trim() === "" || result.trim() === ",") {
          return "";
        }
        return result.replace(/,\s*$/, "");
      }
    }
    return segment;
  }

  const openIdx = startMatch.index + startMatch[0].length - 1;
  let depth = 0;
  for (let i = openIdx; i < segment.length; i++) {
    if (segment[i] === "{") {
      depth++;
    } else if (segment[i] === "}") {
      depth--;
    }
    if (depth === 0) {
      let endIdx = i + 1;
      // Remove trailing comma/whitespace if the leading comma was not consumed
      if (!startMatch[0].includes(",")) {
        const trailing = segment.slice(endIdx);
        const trailingMatch = trailing.match(/^\s*,?\s*/);
        if (trailingMatch) {
          endIdx += trailingMatch[0].length;
        }
      }
      const result = segment.slice(0, startMatch.index) + segment.slice(endIdx);
      if (result.trim() === "" || result.trim() === ",") {
        return "";
      }
      return result.replace(/,\s*$/, "");
    }
  }
  return segment;
};

function createRClient(serverName: "r" | "r_jarl") {
  const resyncRef: { current: (() => Promise<void>) | undefined } = {
    current: undefined,
  };

  const transport = createTransport(serverName, async () => {
    await resyncRef.current?.();
  });

  const notebookClient = new NotebookLanguageServerClient(
    new LanguageServerClient({
      transport,
      rootUri: getLspRootUri("r"),
      workspaceFolders: [],
    }),
    {},
    undefined,
    "r",
  );

  resyncRef.current = () => notebookClient.resyncAllDocuments();

  return notebookClient;
}

/**
 * The R language server, optionally federated with jarl.
 *
 * jarl is a linter, not a full language server: it provides diagnostics and
 * quick fixes and nothing else. Rather than making it an alternative backend,
 * it runs as a second server whose diagnostics are merged with the first's —
 * the same arrangement Python uses to run several type checkers at once. So
 * `languageserver` keeps supplying completions and hover while jarl adds its
 * lint rules on top.
 */
const rLspClient = once((lspConfig: LSPConfig) => {
  const primary = createRClient("r");
  if (!lspConfig?.r?.jarl?.enabled) {
    return primary;
  }
  return new FederatedLanguageServerClient([primary, createRClient("r_jarl")]);
});

export class RLanguageAdapter implements LanguageAdapter<RLanguageMetadata> {
  readonly type = "r" as const;
  readonly defaultCode = '_r_output = mo.r("""\n# Write R code here\n""")';
  readonly defaultMetadata: Readonly<RLanguageMetadata> = {
    prefix: "",
    suffix: "",
    outputVar: DEFAULT_R_OUTPUT,
    showOutput: DEFAULT_R_SHOW_OUTPUT,
    capture: DEFAULT_R_CAPTURE,
    plot: DEFAULT_R_PLOT,
    inputs: "",
    plotFormat: DEFAULT_R_PLOT_FORMAT,
    plotWidth: DEFAULT_R_PLOT_WIDTH,
    plotHeight: DEFAULT_R_PLOT_HEIGHT,
    plotDpi: DEFAULT_R_PLOT_DPI,
  };

  transformIn(code: string): [string, number, RLanguageMetadata] {
    const match = code.match(R_WRAPPER_REGEX);
    if (!match || !match.groups) {
      return [
        code,
        0,
        {
          prefix: "",
          suffix: "",
          outputVar: DEFAULT_R_OUTPUT,
          showOutput: DEFAULT_R_SHOW_OUTPUT,
          capture: DEFAULT_R_CAPTURE,
          plot: DEFAULT_R_PLOT,
          inputs: "",
          plotFormat: DEFAULT_R_PLOT_FORMAT,
          plotWidth: DEFAULT_R_PLOT_WIDTH,
          plotHeight: DEFAULT_R_PLOT_HEIGHT,
          plotDpi: DEFAULT_R_PLOT_DPI,
        },
      ];
    }
    const prefixRaw = match.groups.prefix ?? "";
    const suffixRaw = match.groups.suffix ?? "";
    const outputVar = match.groups.outputVar ?? DEFAULT_R_OUTPUT;
    const args = `${prefixRaw} ${suffixRaw}`;

    // Parse boolean args
    const showOutput = parseBooleanArg(args, "output") ?? DEFAULT_R_SHOW_OUTPUT;
    const capture = parseBooleanArg(args, "capture") ?? DEFAULT_R_CAPTURE;
    const plot = parseBooleanArg(args, "plot") ?? DEFAULT_R_PLOT;

    // Parse new kwargs
    const inputs = parseInputsArg(args) ?? "";
    const plotFormat =
      parseStringArg(args, "plot_format") ?? DEFAULT_R_PLOT_FORMAT;
    const plotWidth = parseIntArg(args, "plot_width") ?? DEFAULT_R_PLOT_WIDTH;
    const plotHeight =
      parseIntArg(args, "plot_height") ?? DEFAULT_R_PLOT_HEIGHT;
    const plotDpi = parseIntArg(args, "plot_dpi") ?? DEFAULT_R_PLOT_DPI;

    // Strip all managed kwargs from prefix/suffix
    let prefix = prefixRaw;
    let suffix = suffixRaw;
    for (const seg of ["prefix", "suffix"] as const) {
      const ref = seg === "prefix" ? prefix : suffix;
      let stripped = stripBooleanArg(
        stripBooleanArg(stripBooleanArg(ref, "output"), "capture"),
        "plot",
      );
      stripped = stripInputsArg(stripped);
      stripped = stripSimpleArg(stripped, "plot_format", '"[^"]*"');
      stripped = stripSimpleArg(stripped, "plot_width", "\\d+");
      stripped = stripSimpleArg(stripped, "plot_height", "\\d+");
      stripped = stripSimpleArg(stripped, "plot_dpi", "\\d+");
      if (seg === "prefix") {
        prefix = stripped;
      } else {
        suffix = stripped;
      }
    }

    // Strip leading/trailing newlines added by transformOut for quote safety,
    // and unescape any escaped triple-quote sequences.
    const rawCode = (match.groups.code ?? "")
      .replace(/^\n/, "")
      .replace(/\n$/, "")
      .replaceAll(String.raw`\"""`, '"""');
    return [
      rawCode,
      0,
      {
        prefix,
        suffix,
        outputVar,
        showOutput,
        capture,
        plot,
        inputs,
        plotFormat,
        plotWidth,
        plotHeight,
        plotDpi,
      },
    ];
  }

  transformOut(code: string, metadata: RLanguageMetadata): [string, number] {
    const prefix = metadata.prefix ?? "";
    const suffix = metadata.suffix ?? "";
    const outputVar = metadata.outputVar || DEFAULT_R_OUTPUT;
    const showOutput = metadata.showOutput ?? DEFAULT_R_SHOW_OUTPUT;
    const capture = metadata.capture ?? DEFAULT_R_CAPTURE;
    const plot = metadata.plot ?? DEFAULT_R_PLOT;
    const inputs = metadata.inputs ?? "";
    const plotFormat = metadata.plotFormat ?? DEFAULT_R_PLOT_FORMAT;
    const plotWidth = metadata.plotWidth ?? DEFAULT_R_PLOT_WIDTH;
    const plotHeight = metadata.plotHeight ?? DEFAULT_R_PLOT_HEIGHT;
    const plotDpi = metadata.plotDpi ?? DEFAULT_R_PLOT_DPI;

    // Build kwargs that differ from defaults
    const outputParam = showOutput ? "" : ", output=False";
    const captureParam = capture ? "" : ", capture=False";
    const plotParam = plot ? "" : ", plot=False";
    const inputsParam = inputs ? `, inputs=${inputs}` : "";
    const plotFormatParam =
      plotFormat !== DEFAULT_R_PLOT_FORMAT
        ? `, plot_format="${plotFormat}"`
        : "";
    const plotWidthParam =
      plotWidth !== DEFAULT_R_PLOT_WIDTH ? `, plot_width=${plotWidth}` : "";
    const plotHeightParam =
      plotHeight !== DEFAULT_R_PLOT_HEIGHT ? `, plot_height=${plotHeight}` : "";
    const plotDpiParam =
      plotDpi !== DEFAULT_R_PLOT_DPI ? `, plot_dpi=${plotDpi}` : "";

    // Escape triple-quote sequences within R code, and use newline padding
    // to prevent R code starting/ending with " from merging with the
    // triple-quote delimiters (e.g. `"TRUE"` -> `""""TRUE""""` is invalid).
    const escapedCode = code.replaceAll('"""', String.raw`\"""`);
    const pythonCode =
      `${outputVar} = mo.r(${prefix}"""\n${escapedCode}\n"""${suffix}` +
      `${inputsParam}${outputParam}${captureParam}${plotParam}` +
      `${plotFormatParam}${plotWidthParam}${plotHeightParam}${plotDpiParam})`;
    return [pythonCode, 0];
  }

  isSupported(code: string): boolean {
    return R_WRAPPER_REGEX.test(code);
  }

  getExtension(
    cellId: CellId,
    completionConfig: CompletionConfig,
    hotkeys: HotkeyProvider,
    _placeholderType: PlaceholderType,
    lspConfig: LSPConfig & { diagnostics?: DiagnosticsConfig },
  ): Extension[] {
    const autocompleteOptions = {
      defaultKeymap: false,
      activateOnTyping: completionConfig.activate_on_typing,
      closeOnBlur: false,
    };

    const completionMatchBefore = /([\w.]+|::|\(|\/|,)$/;

    const hoverOptions = {
      hideOnChange: true,
    };

    const languageSupport = new LanguageSupport(StreamLanguage.define(r));

    if (lspConfig?.r?.enabled && hasCapability("r_lsp")) {
      const client = rLspClient(lspConfig);
      return [
        languageSupport,
        completionKeymap(),
        languageServerWithClient({
          client: client as unknown as LanguageServerClient,
          languageId: "r",
          allowHTMLContent: true,
          useSnippetOnCompletion: true,
          hoverConfig: hoverOptions,
          completionConfig: autocompleteOptions,
          completionMatchBefore,
          diagnosticsEnabled: lspConfig.diagnostics?.enabled ?? false,
          sendIncrementalChanges: false,
          signatureHelpEnabled: true,
          signatureActivateOnTyping: completionConfig.signature_hint_on_typing,
          signatureHelpOptions: {
            position: "above",
          },
          keyboardShortcuts: {
            signatureHelp: hotkeys.getHotkey("cell.signatureHelp").key,
            goToDefinition: hotkeys.getHotkey("cell.goToDefinition").key,
            rename: hotkeys.getHotkey("cell.renameSymbol").key,
          },
          onGoToDefinition: (result) => {
            Logger.debug("onGoToDefinition", result);
            if (client.documentUri === result.uri) {
              return;
            }
            getRequestClient().openFile({
              path: result.uri.replace("file://", ""),
            });
          },
        }),
        documentUri.of(CellDocumentUri.of(cellId)),
      ];
    }

    return [
      languageSupport,
      completionKeymap(),
      autocompletion({
        ...autocompleteOptions,
        override: [rCompletionSource],
      }),
    ];
  }
}
