/* Copyright 2026 Marimo. All rights reserved. */

import { python } from "@codemirror/lang-python";
import { EditorState } from "@codemirror/state";
import { EditorView } from "@codemirror/view";
import { atom } from "jotai";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MockRequestClient } from "@/__mocks__/requests";
import { cellId } from "@/__tests__/branded";
import type { NotebookState } from "@/core/cells/cells";
import { getNotebook } from "@/core/cells/cells";
import type { CellId } from "@/core/cells/ids";
import { notebookCellEditorViews } from "@/core/cells/utils";
import { getResolvedMarimoConfig } from "@/core/config/config";
import type { ConnectionName } from "@/core/datasets/engines";
import { OverridingHotkeyProvider } from "@/core/hotkeys/hotkeys";
import { requestClientAtom } from "@/core/network/requests";
import type { MarimoConfig } from "@/core/network/types";
import { store } from "@/core/state/jotai";
import { type CodemirrorCellActions, cellActionsState } from "../cells/state";
import {
  cellIdState,
  completionConfigState,
  hotkeysProviderState,
  lspConfigState,
  placeholderState,
} from "../config/extension";
import { formatAll, formatEditorViews, formatSQL } from "../format";
import {
  adaptiveLanguageConfiguration,
  switchLanguage,
} from "../language/extension";

const mockRequestClient = MockRequestClient.create();

vi.mock("@/core/cells/cells", () => ({
  getNotebook: vi.fn(),
}));

vi.mock("@/core/cells/utils", () => ({
  notebookCellEditorViews: vi.fn(),
}));

vi.mock("@/core/config/config", () => ({
  getResolvedMarimoConfig: vi.fn(),
  resolvedMarimoConfigAtom: atom({
    display: {
      theme: "light",
    },
  }),
}));

const updateCellCode = vi.fn();
const createdViews: EditorView[] = [];

function createEditor(content: string, cellId: CellId) {
  const completionConfig = {
    activate_on_typing: true,
    signature_hint_on_typing: false,
    copilot: false,
    codeium_api_key: null,
  };
  const hotkeys = new OverridingHotkeyProvider({});
  const state = EditorState.create({
    doc: content,
    extensions: [
      python(),
      adaptiveLanguageConfiguration({
        cellId,
        completionConfig,
        hotkeys,
        placeholderType: "marimo-import",
        lspConfig: {},
      }),
      cellIdState.of(cellId),
      completionConfigState.of(completionConfig),
      hotkeysProviderState.of(hotkeys),
      placeholderState.of("marimo-import"),
      lspConfigState.of({ diagnostics: { enabled: false } }),
      cellActionsState.of({
        updateCellCode,
      } as unknown as CodemirrorCellActions),
    ],
  });

  const view = new EditorView({
    state,
    parent: document.body,
  });

  createdViews.push(view);
  return view;
}

const mockConfig = {
  formatting: { line_length: 88 },
} as MarimoConfig;

beforeEach(() => {
  updateCellCode.mockClear();
  vi.clearAllMocks();
  // Set the mock request client in the atom
  store.set(requestClientAtom, mockRequestClient);
});

afterEach(() => {
  for (const view of createdViews) {
    view.destroy();
  }
});

describe("format", () => {
  describe("formatEditorViews", () => {
    it("should format code in editor views", async () => {
      const cid1 = cellId("1");
      const cid2 = cellId("2");
      const views = {
        [cid1]: createEditor("import numpy as    np", cid1),
        [cid2]: createEditor("import pandas as    pd", cid2),
      };

      const formattedCode1 = "import numpy as np";
      const formattedCode2 = "import pandas as pd";

      mockRequestClient.sendFormat.mockResolvedValueOnce({
        codes: {
          [cid1]: formattedCode1,
          [cid2]: formattedCode2,
        },
      });

      vi.mocked(getResolvedMarimoConfig).mockReturnValueOnce(mockConfig);

      await formatEditorViews(views);

      expect(mockRequestClient.sendFormat).toHaveBeenCalledWith({
        codes: {
          [cid1]: "import numpy as    np",
          [cid2]: "import pandas as    pd",
        },
        lineLength: 88,
        languages: {},
      });

      expect(views[cid1].state.doc.toString()).toBe(formattedCode1);
      expect(views[cid2].state.doc.toString()).toBe(formattedCode2);
      expect(updateCellCode).toHaveBeenCalledWith({
        cellId: cid1,
        code: formattedCode1,
        formattingChange: true,
      });
      expect(updateCellCode).toHaveBeenCalledWith({
        cellId: cid2,
        code: formattedCode2,
        formattingChange: true,
      });
    });

    it("should not update editor if formatted code is same as original", async () => {
      const cid = cellId("1");
      const originalCode = "import numpy as np";
      const views = {
        [cid]: createEditor(originalCode, cid),
      };

      mockRequestClient.sendFormat.mockResolvedValueOnce({
        codes: {
          [cid]: originalCode,
        },
      });

      vi.mocked(getResolvedMarimoConfig).mockReturnValueOnce(mockConfig);

      await formatEditorViews(views);

      expect(views[cid].state.doc.toString()).toBe(originalCode);
      expect(updateCellCode).not.toHaveBeenCalled();
    });
  });

  describe("formatAll", () => {
    it("should format all cells in notebook", async () => {
      const cid1 = cellId("1");
      const cid2 = cellId("2");
      const views = {
        [cid1]: createEditor("import numpy as    np", cid1),
        [cid2]: createEditor("import pandas as    pd", cid2),
      };

      vi.mocked(getNotebook).mockReturnValueOnce({} as NotebookState);
      vi.mocked(notebookCellEditorViews).mockReturnValueOnce(views);
      mockRequestClient.sendFormat.mockResolvedValueOnce({
        codes: {
          [cid1]: "import numpy as np",
          [cid2]: "import pandas as pd",
        },
      });

      vi.mocked(getResolvedMarimoConfig).mockReturnValueOnce(mockConfig);

      await formatAll();

      expect(mockRequestClient.sendFormat).toHaveBeenCalledWith({
        codes: {
          [cid1]: "import numpy as    np",
          [cid2]: "import pandas as    pd",
        },
        lineLength: 88,
        languages: {},
      });

      expect(updateCellCode).toHaveBeenCalledWith({
        cellId: cid1,
        code: "import numpy as np",
        formattingChange: true,
      });
    });
  });

  describe("formatSQL", () => {
    it("should format SQL code", async () => {
      const cid = cellId("1");
      const editor = createEditor("SELECT * FROM table WHERE id = 1", cid);
      switchLanguage(editor, { language: "sql" });

      await formatSQL(editor, "duckdb" as ConnectionName);

      // Check that the SQL was formatted
      expect(editor.state.doc.toString()).toMatchInlineSnapshot(`
        "SELECT
          *
        FROM
          table
        WHERE
          id = 1"
      `);
      expect(updateCellCode).toHaveBeenCalledWith({
        cellId: cid,
        code: editor.state.doc.toString(),
        formattingChange: true,
      });
    });

    it("should not format if language adapter is not SQL", async () => {
      const cid = cellId("1");
      const editor = createEditor("SELECT * FROM table WHERE id = 1", cid);
      switchLanguage(editor, { language: "python" });

      await formatSQL(editor, "duckdb" as ConnectionName);

      // Check that the SQL was not formatted
      expect(editor.state.doc.toString()).toBe(
        "SELECT * FROM table WHERE id = 1",
      );
      expect(updateCellCode).not.toHaveBeenCalled();
    });
  });

  describe("formatR", () => {
    it("should send raw R code and languages map for R cells", async () => {
      const cellId = "1" as CellId;
      const rawRCode = "x  <-   1";
      const view = createEditor(rawRCode, cellId);
      switchLanguage(view, { language: "r" });

      const formattedRCode = "x <- 1";

      mockRequestClient.sendFormat.mockResolvedValueOnce({
        codes: {
          [cellId]: formattedRCode,
        },
      });

      vi.mocked(getResolvedMarimoConfig).mockReturnValueOnce(mockConfig);

      await formatEditorViews({ [cellId]: view });

      // Should send raw R code (not Python-wrapped) with languages map
      expect(mockRequestClient.sendFormat).toHaveBeenCalledWith({
        codes: {
          [cellId]: rawRCode,
        },
        lineLength: 88,
        languages: {
          [cellId]: "r",
        },
      });
    });

    it("should apply formatted R code to editor via replaceEditorContent", async () => {
      const cellId = "1" as CellId;
      const rawRCode = "x  <-   1";
      const view = createEditor(rawRCode, cellId);
      switchLanguage(view, { language: "r" });

      const formattedRCode = "x <- 1";

      mockRequestClient.sendFormat.mockResolvedValueOnce({
        codes: {
          [cellId]: formattedRCode,
        },
      });

      vi.mocked(getResolvedMarimoConfig).mockReturnValueOnce(mockConfig);

      await formatEditorViews({ [cellId]: view });

      // Editor should show raw formatted R code
      expect(view.state.doc.toString()).toBe(formattedRCode);
    });

    it("should update notebook state with Python-wrapped code via transformOut", async () => {
      const cellId = "1" as CellId;
      const rawRCode = "x  <-   1";
      const view = createEditor(rawRCode, cellId);
      switchLanguage(view, { language: "r" });

      const formattedRCode = "x <- 1";

      mockRequestClient.sendFormat.mockResolvedValueOnce({
        codes: {
          [cellId]: formattedRCode,
        },
      });

      vi.mocked(getResolvedMarimoConfig).mockReturnValueOnce(mockConfig);

      await formatEditorViews({ [cellId]: view });

      // Notebook state should get the Python-wrapped version
      expect(updateCellCode).toHaveBeenCalledWith({
        cellId,
        code: '_r_output = mo.r("""\nx <- 1\n""")',
        formattingChange: true,
      });
    });

    it("should handle mixed Python and R cells correctly", async () => {
      const pyCellId = "1" as CellId;
      const rCellId = "2" as CellId;

      const pyView = createEditor("import numpy as    np", pyCellId);
      const rView = createEditor("x  <-   1", rCellId);
      switchLanguage(rView, { language: "r" });

      mockRequestClient.sendFormat.mockResolvedValueOnce({
        codes: {
          [pyCellId]: "import numpy as np",
          [rCellId]: "x <- 1",
        },
      });

      vi.mocked(getResolvedMarimoConfig).mockReturnValueOnce(mockConfig);

      await formatEditorViews({
        [pyCellId]: pyView,
        [rCellId]: rView,
      });

      // Python cell sends getEditorCodeAsPython, R cell sends raw code
      // languages map only includes R cells
      expect(mockRequestClient.sendFormat).toHaveBeenCalledWith({
        codes: {
          [pyCellId]: "import numpy as    np",
          [rCellId]: "x  <-   1",
        },
        lineLength: 88,
        languages: {
          [rCellId]: "r",
        },
      });

      // Python cell updated via updateCellCode with formatted code
      expect(updateCellCode).toHaveBeenCalledWith({
        cellId: pyCellId,
        code: "import numpy as np",
        formattingChange: true,
      });

      // R cell updated via updateCellCode with Python-wrapped code
      expect(updateCellCode).toHaveBeenCalledWith({
        cellId: rCellId,
        code: '_r_output = mo.r("""\nx <- 1\n""")',
        formattingChange: true,
      });

      // Both editors show their formatted code
      expect(pyView.state.doc.toString()).toBe("import numpy as np");
      expect(rView.state.doc.toString()).toBe("x <- 1");
    });

    it("should not update R cell if formatted code is same as original", async () => {
      const cellId = "1" as CellId;
      const rawRCode = "x <- 1";
      const view = createEditor(rawRCode, cellId);
      switchLanguage(view, { language: "r" });

      mockRequestClient.sendFormat.mockResolvedValueOnce({
        codes: {
          [cellId]: rawRCode,
        },
      });

      vi.mocked(getResolvedMarimoConfig).mockReturnValueOnce(mockConfig);

      await formatEditorViews({ [cellId]: view });

      expect(view.state.doc.toString()).toBe(rawRCode);
      expect(updateCellCode).not.toHaveBeenCalled();
    });
  });

  it("should format SQL code with different dialect", async () => {
    const cid = cellId("1");
    const editor = createEditor("SELECT * FROM `table.dot` WHERE id = 1", cid);
    switchLanguage(editor, { language: "sql" });

    await formatSQL(editor, "mysql" as ConnectionName); // mysql uses backticks for identifiers

    expect(editor.state.doc.toString()).toMatchInlineSnapshot(`
      "SELECT
        *
      FROM
        \`table.dot\`
      WHERE
        id = 1"
    `);
    expect(updateCellCode).toHaveBeenCalledWith({
      cellId: cid,
      code: editor.state.doc.toString(),
      formattingChange: true,
    });
  });
});
