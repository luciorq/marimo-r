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
    # R + AnyWidget Interactive Round-Trip

    This notebook demonstrates how to pass data **from R** into a custom
    interactive **anywidget**, let the user interact with it in the
    browser, and then send the user's selection **back to R** for further
    analysis.

    **Data flow:**

    ```
    R (generate data)
        --> Arrow IPC --> Python
            --> anywidget (interactive bar chart)
                --> user clicks bars to select them
                    --> Python reads selection
                        --> R (summarize selected items)
    ```

    The custom widget is defined entirely inline — no third-party widget
    packages needed, just `anywidget` and `traitlets`.
    """)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 1. Generate sample data in R

    R creates a small dataset of fruit sales. The returned `data.frame`
    is automatically serialized as Arrow IPC and received in Python as a
    `pyarrow.Table`.
    """)
    return


@app.cell
def _(mo):
    fruit_arrow = mo.r("""
    set.seed(123)

    fruits <- c("Apple", "Banana", "Cherry", "Date", "Elderberry",
                "Fig", "Grape", "Honeydew")
    sales <- round(runif(length(fruits), min = 50, max = 500))
    rating <- round(runif(length(fruits), min = 1, max = 5), 1)

    df <- data.frame(
      fruit = fruits,
      sales = sales,
      rating = rating,
      stringsAsFactors = FALSE
    )

    cat("Generated", nrow(df), "fruit records\\n")
    df
    """, plot=False)
    return (fruit_arrow,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 2. Prepare data for the widget

    Convert the Arrow table into a simple Python list of dicts that we
    can pass to the anywidget as a JSON-serializable trait.
    """)
    return


@app.cell
def _(fruit_arrow):
    import polars as pl  # type: ignore[import-not-found]

    fruit_df = pl.from_arrow(fruit_arrow)
    fruit_records = fruit_df.to_dicts()
    fruit_records
    return fruit_df, fruit_records, pl


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 3. Interactive bar chart widget

    A custom `anywidget` renders an interactive bar chart using plain
    HTML/CSS/JS. **Click bars** to toggle selection. The selected fruit
    names are synced back to Python via the `selected` traitlet.
    """)
    return


@app.cell
def _(fruit_records, mo):
    import anywidget  # type: ignore[import-not-found]
    import traitlets  # type: ignore[import-not-found]

    class FruitSelector(anywidget.AnyWidget):
        _esm = """
        export default {
          render({ model, el }) {
            const container = document.createElement("div");
            container.style.fontFamily = "system-ui, sans-serif";
            container.style.padding = "12px";
            el.appendChild(container);

            function draw() {
              const items = model.get("items");
              const selected = new Set(model.get("selected"));
              const maxSales = Math.max(...items.map(d => d.sales));

              container.innerHTML = "";

              const title = document.createElement("div");
              title.textContent = "Click bars to select/deselect fruits";
              title.style.cssText = "font-size:14px;color:#666;margin-bottom:12px";
              container.appendChild(title);

              for (const item of items) {
                const row = document.createElement("div");
                row.style.cssText = "display:flex;align-items:center;margin:4px 0;cursor:pointer";

                const isSelected = selected.has(item.fruit);

                const label = document.createElement("span");
                label.textContent = item.fruit;
                label.style.cssText = "width:90px;font-size:13px;font-weight:" +
                  (isSelected ? "bold" : "normal");

                const barBg = document.createElement("div");
                barBg.style.cssText = "flex:1;height:24px;background:#f0f0f0;border-radius:4px;overflow:hidden";

                const bar = document.createElement("div");
                const pct = (item.sales / maxSales) * 100;
                bar.style.cssText = "height:100%;border-radius:4px;transition:width 0.3s;width:" +
                  pct + "%;background:" + (isSelected ? "#2563eb" : "#94a3b8");

                const val = document.createElement("span");
                val.textContent = item.sales;
                val.style.cssText = "width:50px;text-align:right;font-size:12px;color:#666;margin-left:8px";

                barBg.appendChild(bar);
                row.appendChild(label);
                row.appendChild(barBg);
                row.appendChild(val);

                row.addEventListener("click", () => {
                  const sel = new Set(model.get("selected"));
                  if (sel.has(item.fruit)) {
                    sel.delete(item.fruit);
                  } else {
                    sel.add(item.fruit);
                  }
                  model.set("selected", [...sel]);
                  model.save_changes();
                });

                container.appendChild(row);
              }

              const footer = document.createElement("div");
              footer.textContent = selected.size + " of " + items.length + " selected";
              footer.style.cssText = "margin-top:8px;font-size:12px;color:#888";
              container.appendChild(footer);
            }

            draw();
            model.on("change:items", draw);
            model.on("change:selected", draw);
          }
        };
        """

        items = traitlets.List([]).tag(sync=True)
        selected = traitlets.List([]).tag(sync=True)

    widget = mo.ui.anywidget(FruitSelector(items=fruit_records, selected=[]))
    widget
    return (widget,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 4. Reactive selection

    `mo.ui.anywidget` makes the widget reactive. When you click bars
    above, this cell re-runs automatically. We access the `selected`
    trait directly on the wrapped widget.
    """)
    return


@app.cell
def _(fruit_df, mo, pl, widget):
    selected_names = widget.selected

    if selected_names:
        selected_df = fruit_df.filter(pl.col("fruit").is_in(selected_names))
    else:
        selected_df = fruit_df

    n = len(selected_names)
    mo.md(
        f"**Selected:** {', '.join(selected_names) if selected_names else 'all (none clicked)'} "
        f"({n} fruit{'s' if n != 1 else ''})"
    )
    return (selected_df,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 5. Send selection back to R

    The selected subset (a Polars DataFrame) is sent to R via
    `inputs=`. R receives it as an Arrow table and produces a summary
    and a bar chart of the selected fruits.
    """)
    return


@app.cell
def _(mo, selected_df):
    _r_output = mo.r("""
    library(ggplot2)

    df <- as.data.frame(selection)
    n <- nrow(df)

    cat("=== R received", n, "fruits ===\\n")
    cat("Total sales:", sum(df$sales), "\\n")
    cat("Mean rating:", round(mean(df$rating), 2), "\\n\\n")

    df$fruit <- factor(df$fruit, levels = df$fruit[order(df$sales)])

    ggplot(df, aes(x = fruit, y = sales, fill = rating)) +
      geom_col(width = 0.6) +
      geom_text(aes(label = sales), hjust = -0.2, size = 3.5) +
      scale_fill_gradient(low = "#93c5fd", high = "#1d4ed8", name = "Rating") +
      coord_flip() +
      labs(
        title = paste("Selected Fruits:", n, "items"),
        subtitle = paste("Total sales:", sum(df$sales)),
        x = NULL, y = "Sales"
      ) +
      theme_minimal(base_size = 12) +
      theme(plot.title = element_text(face = "bold")) +
      expand_limits(y = max(df$sales) * 1.15)
    """, inputs={"selection": selected_df}, plot_format="svg", plot_width=700, plot_height=400)
    return


if __name__ == "__main__":
    app.run()
