import marimo

__generated_with = "0.24.0"
app = marimo.App()


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell
def _(mo):
    mo.md("""
    # marimo-r example
    """)
    return


@app.cell
def _(mo):
    df = mo.r("""
    df <- data.frame(
      x = 1:5,
      y = c(3, 1, 4, 1, 5)
    )
    df
    """, plot=False)
    return (df,)


@app.cell
def _(mo):
    _r_output = mo.r("""
    plot(1:10, (1:10)^2, col = "steelblue", pch = 19)
    """)
    return


@app.cell
def _(mo):
    _r_output = mo.r("""
    if (requireNamespace("ggplot2", quietly = TRUE)) {
      library(ggplot2)
      p <- ggplot(mtcars, aes(x = wt, y = mpg)) +
        geom_point(color = "firebrick") +
        theme_minimal()
      p
    } else {
      "ggplot2 not installed"
    }
    """)
    return


@app.cell
def _(df):
    import polars as pl  # type: ignore[import-not-found]

    polars_df = pl.from_arrow(df)
    polars_df = polars_df.with_columns(
        (pl.col("x") * 2).alias("x2"),
        (pl.col("y") + 1).alias("y_plus"),
    )
    return (polars_df,)


@app.cell
def _(polars_df):
    import duckdb  # type: ignore[import-not-found]

    con = duckdb.connect()
    relation = con.from_arrow(polars_df.to_arrow())
    summary = relation.aggregate(
        "avg(x2) as avg_x2, avg(y_plus) as avg_y_plus, count(*) as total"
    ).to_df()
    return (summary,)


@app.cell
def _(mo, summary):
    summary_r = mo.r("""
    summary_r <- input_summary
    summary_r
    """, inputs={"input_summary": summary}, plot=False)
    return (summary_r,)


@app.cell
def _(mo, summary):
    _df = mo.sql(
        f"""
        SELECT * FROM summary
        """
    )
    return


@app.cell
def _(mo, summary_r):
    _df = mo.sql(
        f"""
        SELECT * FROM summary_r
        """
    )
    return


@app.cell
def _(summary_r):
    print(summary_r)
    return


@app.cell
def _(summary_r):
    summary_r
    return


if __name__ == "__main__":
    app.run()
