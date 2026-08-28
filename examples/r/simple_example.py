import marimo

__generated_with = "0.21.1"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell
def _():
    x = 5
    return (x,)


@app.cell
def _(x):
    print(x)
    return


@app.cell(hide_code=True)
def _(mo):
    _r_output = mo.r("""x <- 1
    x""")
    return


@app.cell
def _(x):
    print(x + 5)
    return


@app.cell(hide_code=True)
def _(mo):
    _r_output = mo.r("""print(x + 5)""")
    return


@app.cell(hide_code=True)
def _(mo):
    _r_output = mo.r("""x <- 1 + 1

    y <- 1:10

    plot(y)""")
    return


@app.cell(hide_code=True)
def _(mo):
    _r_output = mo.r("""print(x)""")
    return


@app.cell(hide_code=True)
def _(mo):
    _r_output = mo.r("""library(ggplot2)

    plot(ggplot2::ggplot())""")
    return


@app.cell(hide_code=True)
def _(mo):
    _r_output = mo.r("""tibble::as_tibble(mtcars)""")
    return


@app.cell(hide_code=True)
def _(mo):
    _r_output = mo.r("""dat <- datasets::mtcars

    # message(dat[1,1])

    dat""")
    return


@app.cell
def _(mo):
    _r_output = mo.r("""library(condathis)

    print(packageVersion("condathis"))

    condathis::create_env()

    condathis::run("ls")""")
    return


if __name__ == "__main__":
    app.run()
