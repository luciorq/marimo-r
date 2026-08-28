/* Copyright 2026 Marimo. All rights reserved. */

import { getIndentUnit } from "@codemirror/language";
import { StateEffect } from "@codemirror/state";
import type { EditorView } from "@codemirror/view";
import type { DialectOptions } from "sql-formatter";
import { logNever } from "@/utils/assertNever";
import { Logger } from "@/utils/Logger";
import { Objects } from "../../utils/objects";
import { getNotebook } from "../cells/cells";
import type { CellId } from "../cells/ids";
import { notebookCellEditorViews } from "../cells/utils";
import { getResolvedMarimoConfig } from "../config/config";
import type { ConnectionName } from "../datasets/engines";
import { getRequestClient } from "../network/requests";
import { cellActionsState } from "./cells/state";
import { cellIdState } from "./config/extension";
import { languageAdapterState } from "./language/extension";
import { SCHEMA_CACHE } from "./language/languages/sql/completion-store";
import { isKnownDialect } from "./language/languages/sql/utils";
import { languageMetadataField } from "./language/metadata";
import {
  getEditorCodeAsPython,
  updateEditorCodeFromPython,
} from "./language/utils";
import { replaceEditorContent } from "./replace-editor-content";

export const formattingChangeEffect = StateEffect.define<boolean>();

/**
 * Format the code in the editor views via the marimo server,
 * and update the editor views with the formatted code.
 */
export async function formatEditorViews(views: Record<CellId, EditorView>) {
  const { sendFormat } = getRequestClient();

  // Build codes and languages maps, sending raw code for R cells
  const codes: Record<CellId, string> = {};
  const languages: Record<CellId, string> = {};

  for (const [cellId, view] of Objects.entries(views)) {
    const adapter = view.state.field(languageAdapterState);
    if (adapter.type === "r") {
      // Send raw R code, not Python-wrapped
      codes[cellId as CellId] = view.state.doc.toString();
      languages[cellId as CellId] = "r";
    } else {
      codes[cellId as CellId] = getEditorCodeAsPython(view);
    }
  }

  const formatResponse = await sendFormat({
    codes,
    lineLength: getResolvedMarimoConfig().formatting.line_length,
    languages,
  });

  for (const [cellIdString, formattedCode] of Objects.entries(
    formatResponse.codes,
  )) {
    const cellId = cellIdString as CellId;
    const originalCode = codes[cellId];
    const view = views[cellId];

    if (!view) {
      continue;
    }

    // Only update the editor view if the formatted code is different
    // from the original code
    if (formattedCode === originalCode) {
      continue;
    }

    const adapter = view.state.field(languageAdapterState);
    const actions = view.state.facet(cellActionsState);

    if (adapter.type === "r") {
      // For R cells: update notebook state with the Python-wrapped version
      const metadata = view.state.field(languageMetadataField);
      const [pythonCode] = adapter.transformOut(formattedCode, metadata);
      actions.updateCellCode({
        cellId,
        code: pythonCode,
        formattingChange: true,
      });
      // Update editor with raw formatted R code
      replaceEditorContent(view, formattedCode, {
        effects: [formattingChangeEffect.of(true)],
      });
    } else {
      // Python (and other) cells: existing path
      actions.updateCellCode({
        cellId,
        code: formattedCode,
        formattingChange: true,
      });
      updateEditorCodeFromPython(view, formattedCode);
    }
  }
}

/**
 * Format all cells in the notebook.
 */
export function formatAll() {
  const views = notebookCellEditorViews(getNotebook());
  return formatEditorViews(views);
}

/**
 * Format the R code in a single editor view via the marimo server.
 *
 * Analogous to formatSQL — used by the R cell panel format button.
 */
export async function formatR(editor: EditorView) {
  const { sendFormat } = getRequestClient();

  const languageAdapter = editor.state.field(languageAdapterState);
  if (languageAdapter.type !== "r") {
    Logger.error("Language adapter is not R");
    return;
  }

  const cellId = editor.state.facet(cellIdState);
  const rawRCode = editor.state.doc.toString();

  const formatResponse = await sendFormat({
    codes: { [cellId]: rawRCode },
    lineLength: getResolvedMarimoConfig().formatting.line_length,
    languages: { [cellId]: "r" },
  });

  const formattedCode = formatResponse.codes[cellId];
  if (!formattedCode || formattedCode === rawRCode) {
    return;
  }

  // Update notebook state with Python-wrapped version
  const metadata = editor.state.field(languageMetadataField);
  const [pythonCode] = languageAdapter.transformOut(formattedCode, metadata);
  const actions = editor.state.facet(cellActionsState);
  actions.updateCellCode({
    cellId,
    code: pythonCode,
    formattingChange: true,
  });

  // Update editor with raw formatted R code
  replaceEditorContent(editor, formattedCode, {
    effects: [formattingChangeEffect.of(true)],
  });
}

/**
 * Format the SQL code in the editor view.
 *
 * This is currently only used by explicitly clicking the format button.
 * We do not use it for auto-formatting onSave or globally because
 * SQL formatting is much more opinionated than Python formatting, and we
 * don't want to tie the two together (just yet).
 */
export async function formatSQL(editor: EditorView, engine: ConnectionName) {
  // Lazy import sql-formatter
  const { formatDialect } = await import("sql-formatter");

  const sqlDialect = SCHEMA_CACHE.getInternalDialect(engine);
  const formatterDialect = await getSqlFormatterDialect(sqlDialect);

  // Get language adapter
  const languageAdapter = editor.state.field(languageAdapterState);
  const tabWidth = getIndentUnit(editor.state);
  if (languageAdapter.type !== "sql") {
    Logger.error("Language adapter is not SQL");
    return;
  }

  const codeAsSQL = editor.state.doc.toString();
  let formattedSQL: string;
  try {
    formattedSQL = formatDialect(codeAsSQL, {
      dialect: formatterDialect,
      tabWidth: tabWidth,
      useTabs: false,
    });
  } catch (error) {
    Logger.error("Error formatting SQL", { error });
    return;
  }

  // Update Python in the notebook state
  const codeAsPython = languageAdapter.transformIn(formattedSQL)[0];
  const actions = editor.state.facet(cellActionsState);
  const cellId = editor.state.facet(cellIdState);
  actions.updateCellCode({
    cellId,
    code: codeAsPython,
    formattingChange: true,
  });

  // Update editor with formatted SQL
  replaceEditorContent(editor, formattedSQL, {
    effects: [formattingChangeEffect.of(true)],
  });
}

async function getSqlFormatterDialect(
  sqlDialect: string | null,
): Promise<DialectOptions> {
  const {
    bigquery,
    db2,
    db2i,
    duckdb,
    hive,
    mariadb,
    mysql,
    tidb,
    n1ql,
    plsql,
    postgresql,
    redshift,
    spark,
    sqlite,
    sql,
    trino,
    transactsql,
    singlestoredb,
    snowflake,
  } = await import("sql-formatter");

  const defaultDialect = sql;

  if (!sqlDialect || !isKnownDialect(sqlDialect)) {
    return defaultDialect;
  }
  switch (sqlDialect) {
    case "mysql":
      return mysql;
    case "mariadb":
      return mariadb;
    case "postgres":
    case "postgresql":
      return postgresql;
    case "sqlite":
      return sqlite;
    case "db2":
      return db2;
    case "db2i":
      return db2i;
    case "hive":
      return hive;
    case "redshift":
      return redshift;
    case "snowflake":
      return snowflake;
    case "trino":
      return trino;
    case "tidb":
      return tidb;
    case "oracle":
    case "oracledb":
      return plsql;
    case "spark":
      return spark;
    case "singlestoredb":
      return singlestoredb;
    case "couchbase":
      return n1ql;
    case "bigquery":
      return bigquery;
    case "duckdb":
      return duckdb;
    case "mssql":
    case "sqlserver":
    case "microsoft sql server":
      return transactsql;
    case "athena":
    case "awsathena":
    case "cassandra":
    case "noql":
    case "flink":
    case "mongodb":
    case "timescaledb":
    case "datafusion":
    case "dremio":
      return sql;
    case "databricks":
      return spark;
    default:
      logNever(sqlDialect);
      return defaultDialect;
  }
}
