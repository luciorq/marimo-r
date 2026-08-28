/* Copyright 2026 Marimo. All rights reserved. */

import { once } from "@/utils/once";
import { MarkdownLanguageAdapter } from "./languages/markdown";
import { PythonLanguageAdapter } from "./languages/python";
import { RLanguageAdapter } from "./languages/r";
import { SQLLanguageAdapter } from "./languages/sql/sql";
import type { LanguageAdapter, LanguageAdapterType } from "./types";

// Create cached instances
const createPythonAdapter = once(() => new PythonLanguageAdapter());
const createMarkdownAdapter = once(() => new MarkdownLanguageAdapter());
const createSqlAdapter = once(() => new SQLLanguageAdapter());
const createRAdapter = once(() => new RLanguageAdapter());

export const LanguageAdapters: Record<LanguageAdapterType, LanguageAdapter> = {
  // Getters to prevent circular dependencies
  get python() {
    return createPythonAdapter();
  },
  get markdown() {
    return createMarkdownAdapter();
  },
  get sql() {
    return createSqlAdapter();
  },
  get r() {
    return createRAdapter();
  },
};

export function getLanguageAdapters(): LanguageAdapter[] {
  return Object.values(LanguageAdapters);
}
