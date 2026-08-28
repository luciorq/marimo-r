/* Copyright 2026 Marimo. All rights reserved. */
import { cellIdsAtom } from "@/core/cells/cells";
import { lspWorkspaceAtom } from "@/core/saving/file-state";
import { store } from "@/core/state/jotai";
import { CellDocumentUri } from "./types";

/**
 * `CellDocumentUri.is` only tests the `file:///` prefix, which every absolute
 * file URI shares, so on its own it cannot tell a cell from a real file on
 * disk. Resolve the URI against the open notebook instead.
 */
export function isKnownCellDocumentUri(uri: string): boolean {
  if (!CellDocumentUri.is(uri)) {
    return false;
  }
  return store.get(cellIdsAtom).inOrderIds.includes(CellDocumentUri.parse(uri));
}

export function getLspRootUri(_language = "python") {
  const lspWorkspace = store.get(lspWorkspaceAtom);
  // The backend provides rootUri for active notebook sessions.
  // For non-notebook pages (home, gallery), lspWorkspace is null,
  // so return a valid file URI fallback.
  return lspWorkspace?.rootUri ?? "file:///";
}

export function getLspWorkspaceFolders() {
  const lspWorkspace = store.get(lspWorkspaceAtom);
  const rootUri = lspWorkspace?.rootUri;
  // Return workspace folders only if rootUri is set; empty array otherwise.
  return rootUri ? [{ uri: rootUri, name: "marimo" }] : [];
}

export function getLspDocumentUri(language = "python") {
  const lspWorkspace = store.get(lspWorkspaceAtom);
  // The backend provides documentUri for active notebook sessions.
  // For non-notebook pages (home, gallery), lspWorkspace is null,
  // so return a valid file URI fallback.
  const expectedSuffix = language === "r" ? ".r" : ".py";
  const documentUri = lspWorkspace?.documentUri;
  if (!documentUri) {
    return `file:///__marimo_notebook__${expectedSuffix}`;
  }

  if (documentUri.endsWith(expectedSuffix)) {
    return documentUri;
  }

  if (documentUri.endsWith(".py") || documentUri.endsWith(".r")) {
    return `${documentUri.slice(0, -3)}${expectedSuffix}`;
  }

  return documentUri;
}
