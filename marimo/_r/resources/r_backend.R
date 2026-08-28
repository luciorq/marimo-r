options(stringsAsFactors = FALSE)

# Pin the library path to the environment marimo launched us from.
#
# marimo/_r/launcher.py already strips R_LIBS/R_LIBS_USER/R_LIBS_SITE so a
# user's global library cannot shadow the pinned packages. This re-asserts it
# from inside R, which covers anything R's own startup might add back, and makes
# the effective library visible to `.libPaths()` for debugging.
.marimo_lib_paths <- Sys.getenv("MARIMO_R_LIB_PATHS", unset = "")
if (nzchar(.marimo_lib_paths)) {
  .libPaths(strsplit(.marimo_lib_paths, .Platform$path.sep, fixed = TRUE)[[1]])
}

suppressWarnings(suppressMessages(library(jsonlite)))
suppressWarnings(suppressMessages(library(arrow)))

.marimo_env <- new.env(parent = globalenv())

# Check if the R duckdb package is available at startup.
# When it is, Arrow inputs are also registered as DuckDB tables
# so R code can query them with DBI::dbGetQuery().
.has_duckdb <- requireNamespace("duckdb", quietly = TRUE)
.duckdb_con <- NULL
if (.has_duckdb) {
  tryCatch(
    {
      .duckdb_con <- DBI::dbConnect(duckdb::duckdb())
      # Expose the connection in the user environment
      assign(".marimo_duckdb", .duckdb_con, envir = .marimo_env)
    },
    error = function(e) {
      .has_duckdb <<- FALSE
      .duckdb_con <<- NULL
    }
  )
}

read_json_line <- function() {
  line <- readLines(con = file("stdin"), n = 1, warn = FALSE)
  if (length(line) == 0 || is.na(line)) {
    return(NULL)
  }
  fromJSON(line)
}

write_json_line <- function(obj) {
  line <- toJSON(obj, auto_unbox = TRUE, null = "null")
  writeLines(line, con = stdout())
  flush.console()
  flush(stdout())
}

decode_inputs <- function(inputs, env) {
  if (is.null(inputs) || length(inputs) == 0) {
    return(env)
  }
  for (name in names(inputs)) {
    payload <- inputs[[name]]
    if (!is.list(payload) || is.null(payload$type)) {
      env[[name]] <- payload
      next
    }
    if (payload$type == "arrow" || payload$type == "arrow_file") {
      if (payload$type == "arrow") {
        raw_bytes <- base64_dec(payload$data)
        table <- read_ipc_stream(raw_bytes)
      } else {
        # Large payload: read from temp file and delete it
        table <- read_ipc_stream(payload$path)
        tryCatch(file.remove(payload$path), error = function(e) NULL)
      }
      env[[name]] <- table
      # Also register as a DuckDB table for SQL access.
      #
      # read_ipc_stream() defaults to as_data_frame = TRUE, so `table` is a
      # tibble rather than an Arrow Table. duckdb_register() is the entry point
      # for data frames; duckdb_register_arrow() expects an Arrow object and
      # fails on this input, which used to be swallowed by the error handler
      # below and surfaced later as an opaque "Invalid Error: std::exception"
      # from rapi_prepare when the query could not find the table.
      if (.has_duckdb && !is.null(.duckdb_con)) {
        tryCatch(
          {
            duckdb::duckdb_register(.duckdb_con, name, table)
          },
          error = function(e) {
            # Re-register: unregister first, then register
            tryCatch(
              {
                duckdb::duckdb_unregister(.duckdb_con, name)
                duckdb::duckdb_register(.duckdb_con, name, table)
              },
              error = function(e2) NULL
            )
          }
        )
      }
    } else if (payload$type == "value") {
      env[[name]] <- payload$data
    } else {
      env[[name]] <- payload
    }
  }
  env
}

# Payloads larger than this threshold are written to a temp file
# instead of being base64-encoded in the JSON response.
.arrow_file_threshold <- 1048576L # 1 MB

encode_value <- function(value) {
  if (is.null(value)) {
    return(list(kind = "value", data = NULL))
  }
  if (inherits(value, "ArrowTabular")) {
    raw_bytes <- write_to_raw(value)
    if (length(raw_bytes) >= .arrow_file_threshold) {
      path <- tempfile(pattern = "marimo-arrow-", fileext = ".ipc")
      writeBin(raw_bytes, path)
      return(list(kind = "arrow_file", path = path))
    }
    return(list(kind = "arrow", data = base64_enc(raw_bytes)))
  }
  if (is.data.frame(value)) {
    table <- arrow_table(value)
    raw_bytes <- write_to_raw(table)
    if (length(raw_bytes) >= .arrow_file_threshold) {
      path <- tempfile(pattern = "marimo-arrow-", fileext = ".ipc")
      writeBin(raw_bytes, path)
      return(list(kind = "arrow_file", path = path))
    }
    return(list(kind = "arrow", data = base64_enc(raw_bytes)))
  }
  if (is.object(value) && !is.atomic(value) && !is.list(value)) {
    return(list(kind = "value", data = NULL))
  }
  list(kind = "value", data = value)
}

capture_plot <- function(
  expr,
  plot,
  env,
  format = "png",
  width = 960,
  height = 640,
  dpi = 120
) {
  if (!isTRUE(plot)) {
    return(list(path = NULL, error = NULL, value = NULL))
  }
  if (identical(format, "svg")) {
    plot_ext <- ".svg"
  } else {
    plot_ext <- ".png"
    format <- "png"
  }
  plot_file <- tempfile(pattern = "marimo-r-plot-", fileext = plot_ext)
  error_msg <- NULL
  value <- NULL
  plot_detected <- FALSE
  dev_open <- FALSE
  tryCatch(
    {
      if (identical(format, "svg")) {
        svg(filename = plot_file, width = width / dpi, height = height / dpi)
      } else {
        png(filename = plot_file, width = width, height = height, res = dpi)
      }
      dev_open <- TRUE
      usr_before <- tryCatch(par("usr"), error = function(e) NULL)
      value <- eval(expr, envir = env)
      if (inherits(value, "ggplot")) {
        print(value)
        plot_detected <- TRUE
        value <- NULL
      }
      if (!plot_detected) {
        usr_after <- tryCatch(par("usr"), error = function(e) NULL)
        if (!is.null(usr_before) && !is.null(usr_after)) {
          if (!isTRUE(all.equal(usr_before, usr_after))) {
            plot_detected <- TRUE
          }
        }
      }
    },
    error = function(e) {
      error_msg <<- conditionMessage(e)
    }
  )
  # Close the device so the file is fully written before we
  # inspect it. Without this, dev.off() fires via on.exit
  # *after* we already decided whether a plot exists.
  if (dev_open) {
    tryCatch(dev.off(), error = function(e) NULL)
    dev_open <- FALSE
  }
  # Determine whether a real plot was produced.  When par("usr")
  # changes due to graphics-system side-effects (e.g. package
  # loading) we get a false positive — the file exists but is
  # just a blank canvas.  A blank PNG is typically < 1 KB and a
  # blank SVG < 500 bytes.  Real plots are much larger.
  blank_threshold <- if (identical(format, "svg")) 600L else 1000L
  if (file.exists(plot_file)) {
    fsize <- file.info(plot_file)$size
    if (!plot_detected || is.na(fsize) || fsize < blank_threshold) {
      unlink(plot_file)
      plot_file <- NULL
    }
  } else {
    plot_file <- NULL
  }
  list(path = plot_file, error = error_msg, value = value)
}

run_request <- function(request) {
  id <- request$id
  code <- request$code
  inputs <- decode_inputs(request$inputs, .marimo_env)
  capture <- isTRUE(request$capture)
  plot <- isTRUE(request$plot)
  plot_format <- if (!is.null(request$plot_format)) {
    request$plot_format
  } else {
    "png"
  }
  plot_width <- if (!is.null(request$plot_width)) {
    request$plot_width
  } else {
    960L
  }
  plot_height <- if (!is.null(request$plot_height)) {
    request$plot_height
  } else {
    640L
  }
  plot_dpi <- if (!is.null(request$plot_dpi)) {
    request$plot_dpi
  } else {
    120L
  }

  stdout_buffer <- character()
  stderr_buffer <- character()
  value <- NULL
  ok <- TRUE
  error_msg <- NULL
  plot_info <- list(path = NULL, error = NULL)

  eval_env <- inputs
  assign("request_id", id, envir = eval_env)

  tryCatch(
    {
      expr <- parse(text = code)
      if (length(expr) == 0) {
        value <- NULL
      } else {
        if (capture) {
          stdout_buffer <- capture.output(
            {
              withCallingHandlers(
                {
                  if (plot) {
                    plot_info <- capture_plot(
                      expr,
                      plot,
                      eval_env,
                      format = plot_format,
                      width = plot_width,
                      height = plot_height,
                      dpi = plot_dpi
                    )
                    value <- plot_info$value
                  } else {
                    if (length(expr) > 1) {
                      eval(expr[-length(expr)], envir = eval_env)
                    }
                    value <- eval(expr[[length(expr)]], envir = eval_env)
                  }
                },
                message = function(m) {
                  stderr_buffer <<- c(stderr_buffer, conditionMessage(m))
                  invokeRestart("muffleMessage")
                },
                warning = function(w) {
                  stderr_buffer <<- c(stderr_buffer, conditionMessage(w))
                  invokeRestart("muffleWarning")
                }
              )
            },
            type = "output"
          )
        } else {
          withCallingHandlers(
            {
              if (plot) {
                plot_info <- capture_plot(
                  expr,
                  plot,
                  eval_env,
                  format = plot_format,
                  width = plot_width,
                  height = plot_height,
                  dpi = plot_dpi
                )
                value <- plot_info$value
              } else {
                if (length(expr) > 1) {
                  eval(expr[-length(expr)], envir = eval_env)
                }
                value <- eval(expr[[length(expr)]], envir = eval_env)
              }
            },
            message = function(m) {
              stderr_buffer <<- c(stderr_buffer, conditionMessage(m))
              invokeRestart("muffleMessage")
            },
            warning = function(w) {
              stderr_buffer <<- c(stderr_buffer, conditionMessage(w))
              invokeRestart("muffleWarning")
            }
          )
        }
      }
    },
    interrupt = function(e) {
      ok <<- FALSE
      error_msg <<- "Interrupted by user"
    },
    error = function(e) {
      ok <<- FALSE
      error_msg <<- conditionMessage(e)
    }
  )

  encoded <- encode_value(value)
  plot_info$value <- NULL
  response <- list(
    id = id,
    ok = ok,
    stdout = stdout_buffer,
    stderr = stderr_buffer,
    value = encoded,
    plot = plot_info,
    error = error_msg
  )
  response
}

repeat {
  request <- read_json_line()
  if (is.null(request)) {
    break
  }
  if (is.null(request$id)) {
    write_json_line(list(ok = FALSE, error = "Missing request id"))
    next
  }
  if (is.null(request$code)) {
    write_json_line(list(id = request$id, ok = FALSE, error = "Missing code"))
    next
  }
  response <- run_request(request)
  tryCatch(
    write_json_line(response),
    error = function(e) {
      fallback <- list(
        id = request$id,
        ok = FALSE,
        error = paste("JSON serialization error:", conditionMessage(e))
      )
      write_json_line(fallback)
    }
  )
}

# Clean up DuckDB connection on exit
if (.has_duckdb && !is.null(.duckdb_con)) {
  tryCatch(DBI::dbDisconnect(.duckdb_con), error = function(e) NULL)
}
