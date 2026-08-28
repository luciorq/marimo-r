# Copyright 2026 Marimo. All rights reserved.
"""Registrations that wire R support into marimo.

This module is the inventory of every point where R support attaches to the
rest of marimo. Each function below is called from a one-line, greppable
`# marimo-r hook` at the bottom of the upstream module it extends, so the
fork's delta in upstream files stays thin and uniform — and so this file
documents the true coupling surface a future plugin (or an upstream
language-plugin API) would have to cover.

Everything here is import-light on purpose: hooks run at module-import time of
their host, so the heavy R modules are imported inside the functions, only when
the host subsystem actually loads.

Registrations handled here
--------------------------
- LSP servers: `RLanguageServer` and `RJarlServer` into
  `CompositeLspServer.LANGUAGE_SERVERS` (from `_server/lsp.py`).
- Capability tier: `ResetRSessionCommand` into the EDIT tier (from
  `_session/capabilities.py`).

Direct edits that cannot become registrations, and why
------------------------------------------------------
- `_runtime/commands.py` — `ResetRSessionCommand` must be a member of the
  `CommandMessage` typing union, which is closed at class-definition time.
- `_server/models/completion.py` — `Language` is a `Literal`; closed union.
- `_messaging/notification.py` — `r_lsp` is a field on a msgspec struct;
  msgspec classes cannot gain fields at runtime.
- `_runtime/context/types.py` — `r_process` is a dataclass field used by the
  SIGINT handler to forward interrupts to the R child.
- `_runtime/handlers.py`, `_runtime/kernel_request_handlers.py`,
  `_server/api/endpoints/*` — interrupt forwarding, command routing, and the
  reset endpoint; routing is instance-level and could move behind the ASGI
  middleware entry point (`marimo.server.asgi.middleware`) in a true plugin.
- `_server/lsp.py` `BaseLspServer.get_environment()` — the generic hook the R
  servers use to sanitize their environment; upstreamable as-is.
- `_server/api/endpoints/editing.py` — imports `DefaultRFormatter` and routes
  R cells to it in the format endpoint.
- `marimo/__init__.py` — exports `marimo.r`, the public API.
- `_messaging/notification.py` — the `r_lsp` capability probe (msgspec field,
  see above) also calls `find_r_tool` to compute its value.
- The frontend — `LanguageAdapterType` is a closed union in a bundled SPA with
  no extension mechanism; this is the part only a fork (or an upstream
  frontend plugin API) can provide.
"""

from __future__ import annotations


def register_lsp_servers() -> None:
    """Add the R language servers to CompositeLspServer's registry.

    Called from the bottom of `marimo/_r/lsp_servers.py` (self-registration),
    which `marimo/_server/lsp.py` triggers with a plain module import at its
    own bottom. Both hooks sit below every definition they need, so the
    lsp <-> lsp_servers import cycle resolves in either order.
    """
    from marimo._r.lsp_servers import RJarlServer, RLanguageServer
    from marimo._server.lsp import CompositeLspServer

    CompositeLspServer.LANGUAGE_SERVERS["r"] = RLanguageServer
    CompositeLspServer.LANGUAGE_SERVERS["r_jarl"] = RJarlServer


def r_edit_commands() -> frozenset[type]:
    """The R commands that require the EDIT capability tier.

    Called from the bottom of `marimo/_session/capabilities.py`.
    `ResetRSessionCommand` tears down the R subprocess and its state, so it is
    kernel-lifecycle tier alongside `StopKernelCommand`; upstream raises on
    dispatch for any command left untiered.
    """
    from marimo._runtime.commands import ResetRSessionCommand

    return frozenset({ResetRSessionCommand})
