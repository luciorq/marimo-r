# Copyright 2026 Marimo. All rights reserved.

import marimo

__generated_with = "0.23.6"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # R + Polars + DuckDB Interop

    This notebook demonstrates **seamless data exchange** between R, Python
    (Polars), and DuckDB using Apache Arrow as the zero-copy transport layer.

    **Data flow:**

    ```
    R (generate) --> Arrow --> Polars (transform) --> DuckDB (aggregate)
                                                         |
    R (model + plot) <-- Arrow <-- Polars <------------- +
    ```

    Every handoff uses Arrow IPC — no CSV serialization, no copies.
    """)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 1. Setup
    """)
    return


@app.cell
def _():
    import polars as pl  # type: ignore[import-not-found]

    return (pl,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 2. Generate weather data in R

    We use R to create a synthetic weather dataset for 6 cities over 365
    days. R's vectorized sampling and date arithmetic make this concise.
    The returned `data.frame` is automatically serialized as Arrow IPC.
    """)
    return


@app.cell(hide_code=True)
def _(mo):
    weather_arrow = mo.r("""
    set.seed(42)

    cities <- c("Austin", "Boston", "Chicago", "Denver", "Miami", "Seattle")
    n_days <- 365L

    # Base temperatures (F) and rainfall (inches) per city
    base_temp <- c(
      Austin = 78,
      Boston = 52,
      Chicago = 50,
      Denver = 55,
      Miami = 82,
      Seattle = 52
    )
    base_rain <- c(
      Austin = 0.09,
      Boston = 0.12,
      Chicago = 0.10,
      Denver = 0.05,
      Miami = 0.16,
      Seattle = 0.15
    )

    # Expand into a full year of daily observations
    city_col <- rep(cities, each = n_days)
    date_col <- rep(
      seq.Date(as.Date("2025-01-01"), by = "day", length.out = n_days),
      times = length(cities)
    )

    # Seasonal sine wave + daily noise
    day_of_year <- as.integer(format(date_col, "%j"))
    seasonal <- 15 * sin(2 * pi * (day_of_year - 80) / 365)
    temp_f <- base_temp[city_col] + seasonal + rnorm(length(city_col), sd = 5)

    # Rainfall: exponential draws scaled by city baseline
    rain_in <- rexp(length(city_col), rate = 1 / base_rain[city_col])

    weather <- data.frame(
      city = city_col,
      date = date_col,
      temp_f = round(temp_f, 1),
      rain_in = round(rain_in, 3)
    )

    weather
    """, plot=False)
    return (weather_arrow,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 3. Arrow to Polars (zero-copy)

    `mo.r()` returns a `pyarrow.Table`. Polars reads it without copying
    the underlying buffers. We add metric conversions and a 7-day rolling
    average temperature.
    """)
    return


@app.cell
def _(pl, weather_arrow):
    weather = (
        pl.from_arrow(weather_arrow)
        .with_columns(
            # Fahrenheit -> Celsius
            ((pl.col("temp_f") - 32) * 5 / 9).round(1).alias("temp_c"),
            # Inches -> millimeters
            (pl.col("rain_in") * 25.4).round(1).alias("rain_mm"),
        )
        .sort("city", "date")
        .with_columns(
            # 7-day rolling mean temperature per city
            pl.col("temp_c")
            .rolling_mean(window_size=7)
            .over("city")
            .round(1)
            .alias("temp_c_7d"),
        )
    )
    weather
    return (weather,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 4. DuckDB analytics on Polars data

    DuckDB can query Arrow tables directly. We compute monthly aggregates
    per city — average temperature, total rainfall, and the hottest day.
    """)
    return


@app.cell
def _(mo, weather):
    monthly_stats = mo.sql(
        f"""
        SELECT
            city,
            DATE_TRUNC('month', date) AS month,
            ROUND(AVG(temp_c), 1) AS avg_temp_c,
            ROUND(SUM(rain_mm), 1) AS total_rain_mm,
            ROUND(MAX(temp_c), 1) AS max_temp_c,
            COUNT(*) AS n_days
        FROM weather
        GROUP BY city, DATE_TRUNC('month', date)
        ORDER BY city, month
        """
    )
    return (monthly_stats,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 5. Send aggregated data back to R

    The DuckDB result (a Polars DataFrame) is passed into R via the
    `inputs=` parameter. marimo serializes it as Arrow IPC automatically.
    R receives it as an Arrow Table, which it can convert to a
    `data.frame` with `as.data.frame()`.
    """)
    return


@app.cell
def _(mo, monthly_stats):
    r_summary = mo.r("""
    stats <- as.data.frame(monthly_stats)

    # Month label for readability
    stats$month_label <- format(stats$month, "%b")

    # Classify months by temperature
    stats$season <- ifelse(
      stats$avg_temp_c >= 20,
      "warm",
      ifelse(stats$avg_temp_c >= 10, "mild", "cold")
    )

    cat("Rows received from DuckDB:", nrow(stats), "\n")
    cat("Cities:", paste(unique(stats$city), collapse = ", "), "\n")
    cat("Season breakdown:\n")
    print(table(stats$season))

    stats
    """, inputs={"monthly_stats": monthly_stats}, plot=False)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 6. Statistical modeling in R

    We send the daily weather data back into R to fit a linear model
    predicting temperature from rainfall and city. R's `lm()` is
    purpose-built for this; the model summary stays in R's console
    output while we extract the coefficients as a data frame.
    """)
    return


@app.cell
def _(mo, weather):
    model_coefs = mo.r("""
    w <- as.data.frame(daily_weather)

    # Fit: temp_c ~ rain_mm + city
    fit <- lm(temp_c ~ rain_mm + city, data = w)

    cat("=== Model Summary ===\n")
    s <- summary(fit)
    cat("R-squared:", round(s$r.squared, 4), "\n")
    cat(
      "F-statistic:",
      round(s$fstatistic[1], 2),
      "on",
      s$fstatistic[2],
      "and",
      s$fstatistic[3],
      "df\n\n"
    )

    # Extract coefficients as a tidy data frame
    coef_df <- data.frame(
      term = rownames(s$coefficients),
      estimate = round(s$coefficients[, "Estimate"], 4),
      std_error = round(s$coefficients[, "Std. Error"], 4),
      p_value = signif(s$coefficients[, "Pr(>|t|)"], 4),
      row.names = NULL
    )
    coef_df
    """, inputs={"daily_weather": weather})
    return (model_coefs,)


@app.cell
def _(model_coefs, pl):
    coefs = pl.from_arrow(model_coefs)
    coefs
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 7. Visualization in R

    Send the monthly stats back to R for a ggplot2 faceted heatmap.
    This demonstrates a complete round-trip:

    **R** (raw data) -> **Polars** (transform) -> **DuckDB** (aggregate)
    -> **R** (visualize)
    """)
    return


@app.cell
def _(mo, monthly_stats):
    _r_output = mo.r("""
    library(ggplot2)

    stats <- as.data.frame(monthly_stats)
    stats$month_label <- factor(
      format(stats$month, "%b"),
      levels = c(
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec"
      )
    )

    ggplot(stats, aes(x = month_label, y = city, fill = avg_temp_c)) +
      geom_tile(color = "white", linewidth = 0.3) +
      geom_text(aes(label = avg_temp_c), size = 2.8, color = "grey20") +
      scale_fill_gradient2(
        low = "#2166ac",
        mid = "#f7f7f7",
        high = "#b2182b",
        midpoint = 15,
        name = "Avg Temp (C)"
      ) +
      labs(
        title = "Monthly Average Temperature by City",
        subtitle = "Data: R -> Polars -> DuckDB -> R (ggplot2)",
        x = NULL,
        y = NULL
      ) +
      theme_minimal(base_size = 12) +
      theme(
        panel.grid = element_blank(),
        plot.title = element_text(face = "bold")
      )
    """, inputs={"monthly_stats": monthly_stats}, plot_format="svg", plot_width=1000, plot_height=500)
    return


@app.cell
def _(mo, weather):
    _r_output = mo.r("""
    library(ggplot2)

    w <- as.data.frame(daily_weather)
    w$date <- as.Date(w$date)

    ggplot(w, aes(x = date, y = temp_c_7d, color = city)) +
      geom_line(linewidth = 0.6, na.rm = TRUE) +
      scale_color_brewer(palette = "Set2", name = "City") +
      labs(
        title = "7-Day Rolling Average Temperature",
        subtitle = "Computed in Polars, visualized in R",
        x = NULL,
        y = "Temperature (C)"
      ) +
      theme_minimal(base_size = 12) +
      theme(
        legend.position = "bottom",
        plot.title = element_text(face = "bold")
      )
    """, inputs={"daily_weather": weather}, plot_format="svg", plot_width=1000, plot_height=500)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 8. SQL on R-produced Arrow tables

    Arrow tables returned by `mo.r()` are available to `mo.sql()` by
    name. Here we query the coefficient table produced by R's `lm()`.
    """)
    return


@app.cell
def _(mo, model_coefs):
    significant = mo.sql(
        f"""
        SELECT term, estimate, p_value
        FROM model_coefs
        WHERE p_value < 0.05
        ORDER BY ABS(estimate) DESC
        """
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 9. Rainfall analysis

    Use DuckDB to find the rainiest months, then send the top results
    back to R for a bar chart.
    """)
    return


@app.cell
def _(mo, weather):
    rainy_months = mo.sql(
        f"""
        SELECT
            city,
            DATE_TRUNC('month', date) AS month,
            ROUND(SUM(rain_mm), 1) AS total_rain_mm,
            COUNT(*) FILTER (WHERE rain_mm > 5) AS heavy_rain_days
        FROM weather
        GROUP BY city, DATE_TRUNC('month', date)
        HAVING SUM(rain_mm) > 50
        ORDER BY total_rain_mm DESC
        LIMIT 20
        """
    )
    return (rainy_months,)


@app.cell
def _(mo, rainy_months):
    _r_output = mo.r("""
    library(ggplot2)

    df <- as.data.frame(rainy_months)
    df$label <- paste0(df$city, " (", format(df$month, "%b"), ")")
    df$label <- factor(df$label, levels = df$label[order(df$total_rain_mm)])

    ggplot(df, aes(x = total_rain_mm, y = label, fill = city)) +
      geom_col(show.legend = FALSE) +
      geom_text(aes(label = paste0(heavy_rain_days, "d")), hjust = -0.2, size = 3) +
      scale_fill_brewer(palette = "Set2") +
      labs(
        title = "Rainiest City-Months",
        subtitle = "Labels show number of heavy rain days (> 5mm)",
        x = "Total Rainfall (mm)",
        y = NULL
      ) +
      theme_minimal(base_size = 11) +
      theme(plot.title = element_text(face = "bold"))
    """, inputs={"rainy_months": rainy_months}, plot_format="svg", plot_width=900, plot_height=600)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 10. Base R graphics on Python-transformed data

    Not everything needs ggplot2 — R's base `plot()` works too. Here we
    send the daily Polars DataFrame into R and make a temperature vs
    rainfall scatter plot using base graphics.
    """)
    return


@app.cell
def _(mo, weather):
    _r_output = mo.r("""
    w <- as.data.frame(daily_weather)

    # Color by city
    city_colors <- c(
      Austin = "#e41a1c",
      Boston = "#377eb8",
      Chicago = "#4daf4a",
      Denver = "#984ea3",
      Miami = "#ff7f00",
      Seattle = "#a65628"
    )
    cols <- city_colors[w$city]

    plot(
      w$rain_mm,
      w$temp_c,
      col = adjustcolor(cols, alpha.f = 0.3),
      pch = 16,
      cex = 0.6,
      xlab = "Rainfall (mm)",
      ylab = "Temperature (C)",
      main = "Daily Temperature vs Rainfall"
    )
    legend(
      "topright",
      legend = names(city_colors),
      col = city_colors,
      pch = 16,
      cex = 0.7,
      bty = "n"
    )
    """, inputs={"daily_weather": weather})
    return


if __name__ == "__main__":
    app.run()
