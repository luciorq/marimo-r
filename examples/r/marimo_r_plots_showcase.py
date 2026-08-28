import marimo

__generated_with = "0.21.1"
app = marimo.App()


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell
def _(mo):
    mo.md("""
    # marimo-r: Plot showcase
    """)
    return


@app.cell
def _(mo):
    mo.r(
        """
        plot(1:12, (1:12) ^ 2,
             col = "steelblue",
             pch = 19,
             main = "Base R scatter",
             xlab = "index",
             ylab = "value")
        """
    )
    return


@app.cell
def _(mo):
    mo.r(
        """
        if (requireNamespace("ggplot2", quietly = TRUE)) {
          library(ggplot2)
          p <- ggplot(mtcars, aes(x = wt, y = mpg, color = factor(cyl))) +
            geom_point(size = 3) +
            theme_minimal() +
            labs(title = "ggplot2 scatter", color = "cyl")
          p
        } else {
          "ggplot2 not installed"
        }
        """
    )
    return


@app.cell
def _(mo):
    mo.r(
        """
        values <- c(12, 9, 14, 6, 10, 8)
        barplot(values,
                col = "tomato",
                border = NA,
                main = "Base R barplot",
                names.arg = c("A", "B", "C", "D", "E", "F"))
        """
    )
    return


if __name__ == "__main__":
    app.run()
