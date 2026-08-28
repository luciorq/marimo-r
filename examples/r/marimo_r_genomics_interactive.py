# Copyright 2026 Marimo. All rights reserved.

import marimo

__generated_with = "0.21.1"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Interactive Genomics: R + Altair + DuckDB

    This notebook demonstrates a **genomics data analysis pipeline** that
    combines R's statistical strengths with Python's interactive
    visualization ecosystem. All genomic data is simulated in R (no
    Bioconductor needed) and transferred to Python via Apache Arrow IPC.

    **Pipeline:**

    ```
    R (simulate GWAS + RNA-seq)
        --> Arrow IPC
            --> Polars (transform: -log10 p, log2 FC)
                --> DuckDB (filter, aggregate)
                    --> Altair (interactive Manhattan + volcano plots)
                        --> R (ggplot2 summaries of selected regions)
    ```

    **Key features demonstrated:**
    - R generates realistic genomic data using base R stats functions
    - Zero-copy Arrow transfer between R and Python
    - Interactive Altair charts with brush selection (`mo.ui.altair_chart`)
    - Reactive data flow: selecting variants in one chart updates downstream cells
    - DuckDB queries on Arrow tables from both R and Polars
    - R ggplot2 plots driven by Python-filtered data
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
    import math

    import altair as alt  # type: ignore[import-not-found]
    import pandas as pd  # type: ignore[import-not-found]
    import polars as pl  # type: ignore[import-not-found]

    return alt, math, pd, pl


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 2. Simulate GWAS variant data in R

    We simulate a genome-wide association study (GWAS) result set with
    ~10,000 SNP variants across 22 autosomes. Most variants are null
    (uniform p-values), but we spike in association signals on
    chromosomes 6, 9, and 17 to create visible peaks in the Manhattan
    plot.
    """)
    return


@app.cell
def _(mo):
    gwas_arrow = mo.r(
        """
    set.seed(2025)

    n_variants <- 10000L

    # Assign chromosomes with realistic proportions (larger chr = more SNPs)
    chr_sizes <- c(249, 243, 198, 191, 181, 171, 159, 145, 138, 133,
                   135, 133, 114, 107, 102, 90, 83, 80, 59, 64, 47, 51)
    chr_probs <- chr_sizes / sum(chr_sizes)
    chr <- sample(1:22, n_variants, replace = TRUE, prob = chr_probs)

    # Random positions within each chromosome (in megabases)
    pos_mb <- runif(n_variants, min = 1, max = chr_sizes[chr])

    # P-values: mostly null (uniform), with signal spikes on chr 6, 9, 17
    pval <- runif(n_variants)

    # Spike signals: mix of strong and moderate associations
    signal_chr6 <- chr == 6 & pos_mb > 25 & pos_mb < 35
    signal_chr9 <- chr == 9 & pos_mb > 90 & pos_mb < 110
    signal_chr17 <- chr == 17 & pos_mb > 40 & pos_mb < 55

    pval[signal_chr6] <- 10^(-runif(sum(signal_chr6), 4, 12))
    pval[signal_chr9] <- 10^(-runif(sum(signal_chr9), 3, 9))
    pval[signal_chr17] <- 10^(-runif(sum(signal_chr17), 5, 15))

    # Effect sizes (beta): correlated with significance
    beta <- rnorm(n_variants, mean = 0, sd = 0.05)
    beta[signal_chr6] <- rnorm(sum(signal_chr6), mean = 0.3, sd = 0.1)
    beta[signal_chr9] <- rnorm(sum(signal_chr9), mean = -0.2, sd = 0.08)
    beta[signal_chr17] <- rnorm(sum(signal_chr17), mean = 0.4, sd = 0.12)

    # Minor allele frequency
    maf <- rbeta(n_variants, 2, 8)

    # Build SNP IDs
    snp_id <- paste0("rs", sample(100000:9999999, n_variants))

    gwas <- data.frame(
      snp_id = snp_id,
      chr = chr,
      pos_mb = round(pos_mb, 3),
      pvalue = pval,
      beta = round(beta, 5),
      maf = round(maf, 4),
      stringsAsFactors = FALSE
    )

    cat("GWAS variants:", nrow(gwas), "\\n")
    cat("Signal regions spiked on chr 6, 9, 17\\n")
    cat("Genome-wide significant (p < 5e-8):", sum(gwas$pvalue < 5e-8), "\\n")

    gwas
    """,
        plot=False,
    )
    return (gwas_arrow,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 3. Simulate RNA-seq differential expression in R

    We simulate a differential expression experiment (e.g., treatment vs
    control) with ~2,000 genes. Expression counts are drawn from negative
    binomial distributions, then we compute log2 fold change and p-values
    using R's `wilcox.test`. A subset of genes are spiked as truly
    differentially expressed.
    """)
    return


@app.cell
def _(mo):
    de_arrow = mo.r(
        """
    set.seed(42)

    n_genes <- 2000L
    n_samples <- 6L  # 3 control + 3 treatment

    gene_names <- paste0("GENE_", sprintf("%04d", 1:n_genes))

    # Assign genes to chromosomes for cross-referencing with GWAS
    gene_chr <- sample(1:22, n_genes, replace = TRUE)

    # Base expression (mean counts per gene)
    base_expr <- exp(rnorm(n_genes, mean = 5, sd = 1.5))

    # Spike differential expression in ~10% of genes
    n_de <- as.integer(n_genes * 0.10)
    de_idx <- sample(n_genes, n_de)
    fold_changes <- rep(1.0, n_genes)
    fold_changes[de_idx] <- 2^(rnorm(n_de, mean = 0, sd = 1.5))

    # Generate count matrices (negative binomial)
    ctrl_counts <- matrix(
      rnbinom(n_genes * 3, mu = base_expr, size = 10),
      nrow = n_genes, ncol = 3
    )
    treat_counts <- matrix(
      rnbinom(n_genes * 3, mu = base_expr * fold_changes, size = 10),
      nrow = n_genes, ncol = 3
    )

    # Compute per-gene statistics
    log2fc <- numeric(n_genes)
    pval <- numeric(n_genes)
    mean_ctrl <- numeric(n_genes)
    mean_treat <- numeric(n_genes)

    for (i in seq_len(n_genes)) {
      c_vals <- ctrl_counts[i, ]
      t_vals <- treat_counts[i, ]
      mean_ctrl[i] <- mean(c_vals)
      mean_treat[i] <- mean(t_vals)

      # Add pseudocount for log2FC
      log2fc[i] <- log2((mean(t_vals) + 1) / (mean(c_vals) + 1))

      # Wilcoxon test (non-parametric, works with small n)
      if (sd(c(c_vals, t_vals)) > 0) {
        wt <- suppressWarnings(wilcox.test(c_vals, t_vals))
        pval[i] <- wt$p.value
      } else {
        pval[i] <- 1.0
      }
    }

    de_results <- data.frame(
      gene = gene_names,
      chr = gene_chr,
      mean_ctrl = round(mean_ctrl, 1),
      mean_treat = round(mean_treat, 1),
      log2fc = round(log2fc, 4),
      pvalue = pval,
      is_spiked_de = seq_len(n_genes) %in% de_idx,
      stringsAsFactors = FALSE
    )

    cat("Genes:", nrow(de_results), "\\n")
    cat("Spiked DE genes:", n_de, "\\n")
    cat("Significant (p < 0.05):", sum(de_results$pvalue < 0.05), "\\n")

    de_results
    """,
        plot=False,
    )
    return (de_arrow,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 4. Polars transformations

    Convert the Arrow tables to Polars DataFrames and compute derived
    columns needed for visualization: `-log10(p-value)` for the Manhattan
    and volcano plots, cumulative chromosome positions for the x-axis,
    and significance classifications.
    """)
    return


@app.cell
def _(gwas_arrow, math, pl):
    # GWAS: add -log10(p) and cumulative genomic position
    gwas = pl.from_arrow(gwas_arrow).with_columns(
        pl.col("pvalue")
        .map_elements(
            lambda p: -math.log10(max(p, 1e-300)), return_dtype=pl.Float64
        )
        .alias("neg_log10_p"),
    )

    # Compute cumulative chromosome offsets for Manhattan plot x-axis
    chr_offsets = (
        gwas.group_by("chr")
        .agg(pl.col("pos_mb").max().alias("chr_len"))
        .sort("chr")
        .with_columns(
            pl.col("chr_len").cum_sum().shift(1, fill_value=0).alias("offset")
        )
    )

    gwas = gwas.join(
        chr_offsets.select("chr", "offset"), on="chr"
    ).with_columns(
        (pl.col("pos_mb") + pl.col("offset")).alias("genome_pos"),
    )
    gwas
    return chr_offsets, gwas


@app.cell
def _(de_arrow, math, pl):
    # RNA-seq: add -log10(p) and classify significance
    de_genes = (
        pl.from_arrow(de_arrow)
        .with_columns(
            pl.col("pvalue")
            .map_elements(
                lambda p: -math.log10(max(p, 1e-300)), return_dtype=pl.Float64
            )
            .alias("neg_log10_p"),
        )
        .with_columns(
            pl.when((pl.col("pvalue") < 0.05) & (pl.col("log2fc").abs() > 1.0))
            .then(
                pl.when(pl.col("log2fc") > 0)
                .then(pl.lit("Up"))
                .otherwise(pl.lit("Down"))
            )
            .otherwise(pl.lit("NS"))
            .alias("direction"),
        )
    )
    de_genes
    return (de_genes,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 5. DuckDB analytics on GWAS data

    Use SQL to find genome-wide significant variants, aggregate signal
    density per chromosome, and identify the top loci.
    """)
    return


@app.cell
def _(gwas, mo):
    top_loci = mo.sql(
        f"""
        SELECT
            chr,
            ROUND(MIN(pos_mb)::DOUBLE, 1) AS region_start_mb,
            ROUND(MAX(pos_mb)::DOUBLE, 1) AS region_end_mb,
            COUNT(*) AS n_significant,
            GREATEST(MIN(pvalue), 2e-16) AS best_pvalue,
            ROUND(MAX(neg_log10_p)::DOUBLE, 2) AS peak_score,
            ROUND(AVG(ABS(beta))::DOUBLE, 4) AS mean_abs_beta
        FROM gwas
        WHERE pvalue < 1e-4
        GROUP BY chr
        ORDER BY best_pvalue ASC
        """
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 6. Interactive Manhattan plot

    An Altair chart with brush selection. **Click and drag** to select a
    region — the selected variants will flow into downstream cells for
    further analysis in R.
    """)
    return


@app.cell
def _(alt, chr_offsets, gwas, math, mo, pl):
    # Compute chromosome midpoints for x-axis labels
    chr_mids = chr_offsets.with_columns(
        (pl.col("offset") + pl.col("chr_len") / 2).alias("mid")
    )
    mid_map = dict(
        zip(
            chr_mids["chr"].to_list(),
            chr_mids["mid"].to_list(),
            strict=False,
        )
    )

    gwas_df = gwas.to_pandas()

    _brush = alt.selection_interval()

    manhattan = (
        alt.Chart(gwas_df)
        .mark_circle(size=20, opacity=0.6)
        .encode(
            x=alt.X("genome_pos:Q", title="Genomic position")
            .scale(zero=False)
            .axis(
                values=list(mid_map.values()),
                labelExpr=" : ".join(
                    f"datum.value == {v} ? '{k}'"
                    for k, v in sorted(mid_map.items(), key=lambda x: x[1])
                )
                + " : ''",
            ),
            y=alt.Y("neg_log10_p:Q", title="-log10(p-value)"),
            color=alt.condition(
                _brush,
                alt.Color("chr:N", legend=None).scale(scheme="category20"),
                alt.value("lightgrey"),
            ),
            tooltip=["snp_id", "chr", "pos_mb", "pvalue", "beta", "maf"],
        )
        .properties(
            width=800, height=350, title="Manhattan Plot — GWAS Results"
        )
        .add_params(_brush)
    )

    # Genome-wide significance line
    sig_line = (
        alt.Chart(gwas_df.head(1))
        .mark_rule(color="red", strokeDash=[4, 4])
        .encode(y=alt.datum(-math.log10(5e-8)))
    )

    manhattan_chart = mo.ui.altair_chart(manhattan + sig_line)
    manhattan_chart
    return (manhattan_chart,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 7. Interactive Volcano plot — Differential Expression

    Each point is a gene. X-axis shows log2 fold change, y-axis shows
    statistical significance. **Brush to select** genes of interest.
    """)
    return


@app.cell
def _(alt, de_genes, math, mo):
    de_df = de_genes.to_pandas()

    _brush_volcano = alt.selection_interval()

    volcano = (
        alt.Chart(de_df)
        .mark_circle(size=25, opacity=0.5)
        .encode(
            x=alt.X("log2fc:Q", title="log2(Fold Change)").scale(
                domain=[-6, 6]
            ),
            y=alt.Y("neg_log10_p:Q", title="-log10(p-value)"),
            color=alt.condition(
                _brush_volcano,
                alt.Color(
                    "direction:N",
                    scale=alt.Scale(
                        domain=["Up", "Down", "NS"],
                        range=["#e41a1c", "#377eb8", "#999999"],
                    ),
                    legend=alt.Legend(title="Direction"),
                ),
                alt.value("lightgrey"),
            ),
            tooltip=[
                "gene",
                "chr",
                "log2fc",
                "pvalue",
                "mean_ctrl",
                "mean_treat",
            ],
        )
        .properties(
            width=600,
            height=400,
            title="Volcano Plot — Differential Expression",
        )
        .add_params(_brush_volcano)
    )

    # Significance thresholds
    fc_lines = alt.Chart(de_df.head(1)).mark_rule(
        color="grey", strokeDash=[3, 3]
    ).encode(x=alt.datum(-1)) + alt.Chart(de_df.head(1)).mark_rule(
        color="grey", strokeDash=[3, 3]
    ).encode(x=alt.datum(1))
    pval_line = (
        alt.Chart(de_df.head(1))
        .mark_rule(color="grey", strokeDash=[3, 3])
        .encode(y=alt.datum(-math.log10(0.05)))
    )

    volcano_chart = mo.ui.altair_chart(volcano + fc_lines + pval_line)
    volcano_chart
    return (volcano_chart,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 8. Reactive selections

    The charts above are wrapped with `mo.ui.altair_chart`, so brush
    selections automatically propagate as Polars DataFrames. Below we
    show what was selected.
    """)
    return


@app.cell
def _(manhattan_chart, mo, pd, pl):
    # Layered charts return [] when nothing is selected; handle gracefully
    _mval = manhattan_chart.value
    selected_variants = (
        pl.from_pandas(_mval)
        if isinstance(_mval, pd.DataFrame)
        else pl.DataFrame()
    )
    n_sel = len(selected_variants)
    mo.md(
        f"**Manhattan selection:** {n_sel} variant{'s' if n_sel != 1 else ''} selected"
    )
    return (selected_variants,)


@app.cell
def _(mo, pd, pl, volcano_chart):
    # Layered charts return [] when nothing is selected; handle gracefully
    _vval = volcano_chart.value
    selected_genes = (
        pl.from_pandas(_vval)
        if isinstance(_vval, pd.DataFrame)
        else pl.DataFrame()
    )
    n_sel_genes = len(selected_genes)
    mo.md(
        f"**Volcano selection:** {n_sel_genes} gene{'s' if n_sel_genes != 1 else ''} selected"
    )
    return (selected_genes,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 9. Visualize selected variants in R

    When you brush the Manhattan plot, the selected variants are sent
    to R via Arrow IPC for a detailed regional plot using ggplot2.
    If nothing is selected, all genome-wide significant variants are
    shown.
    """)
    return


@app.cell
def _(gwas, mo, pl, selected_variants):
    # If user hasn't selected anything, show genome-wide significant hits
    variants_for_r = (
        selected_variants
        if len(selected_variants) > 0
        else gwas.filter(pl.col("pvalue") < 1e-4)
    )

    _r_output = mo.r(
        """
    library(ggplot2)

    df <- as.data.frame(variants)
    df$chr <- factor(df$chr)

    ggplot(df, aes(x = pos_mb, y = neg_log10_p, color = chr)) +
      geom_point(aes(size = abs(beta)), alpha = 0.7) +
      scale_size_continuous(range = c(1, 5), name = "|Effect size|") +
      labs(
        title = paste("Selected Region:", nrow(df), "variants"),
        x = "Position (Mb)",
        y = "-log10(p-value)",
        color = "Chr"
      ) +
      theme_minimal(base_size = 12) +
      theme(
        plot.title = element_text(face = "bold"),
        legend.position = "right"
      )
    """,
        inputs={"variants": variants_for_r},
        plot_format="svg",
        plot_width=900,
        plot_height=400,
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 10. Expression profile of selected genes

    Genes selected from the volcano plot are sent back to R, where we
    show a side-by-side barplot of control vs treatment expression.
    """)
    return


@app.cell
def _(de_genes, mo, selected_genes):
    # If nothing selected, show top DE genes by significance
    genes_for_r = (
        selected_genes
        if len(selected_genes) > 0
        else de_genes.sort("pvalue").head(20)
    )

    # Limit to top 30 for readability
    genes_for_r_top = genes_for_r.sort("pvalue").head(30)

    _r_output = mo.r(
        """
    library(ggplot2)

    df <- as.data.frame(gene_data)

    # Reshape for grouped barplot
    df_long <- data.frame(
      gene = rep(df$gene, 2),
      condition = rep(c("Control", "Treatment"), each = nrow(df)),
      expression = c(df$mean_ctrl, df$mean_treat),
      log2fc = rep(df$log2fc, 2),
      stringsAsFactors = FALSE
    )

    # Order genes by fold change
    gene_order <- df$gene[order(df$log2fc)]
    df_long$gene <- factor(df_long$gene, levels = gene_order)

    ggplot(df_long, aes(x = gene, y = expression, fill = condition)) +
      geom_col(position = "dodge", width = 0.7) +
      scale_fill_manual(
        values = c(Control = "#4393c3", Treatment = "#d6604d"),
        name = "Condition"
      ) +
      coord_flip() +
      labs(
        title = paste("Expression:", nrow(df), "selected genes"),
        subtitle = "Ordered by log2 fold change",
        x = NULL, y = "Mean expression (counts)"
      ) +
      theme_minimal(base_size = 11) +
      theme(
        plot.title = element_text(face = "bold"),
        legend.position = "bottom"
      )
    """,
        inputs={"gene_data": genes_for_r_top},
        plot_format="svg",
        plot_width=800,
        plot_height=600,
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 11. Cross-reference GWAS hits with DE genes

    Use DuckDB to join the GWAS signal regions with differentially
    expressed genes on the same chromosomes. This identifies
    chromosomes where both genetic association and expression changes
    co-occur — a common integrative genomics analysis.
    """)
    return


@app.cell
def _(de_genes, gwas, mo):
    cross_ref = mo.sql(
        f"""
        WITH gwas_signals AS (
            SELECT
                chr,
                COUNT(*) AS n_gwas_hits,
                GREATEST(MIN(pvalue), 2e-16) AS best_gwas_p,
                ROUND(AVG(ABS(beta))::DOUBLE, 4) AS mean_gwas_effect
            FROM gwas
            WHERE pvalue < 1e-4
            GROUP BY chr
        ),
        de_signals AS (
            SELECT
                chr,
                COUNT(*) FILTER (WHERE direction != 'NS') AS n_de_genes,
                GREATEST(MIN(pvalue), 2e-16) AS best_de_p,
                ROUND(AVG(ABS(log2fc))::DOUBLE, 4) AS mean_abs_log2fc
            FROM de_genes
            GROUP BY chr
        )
        SELECT
            g.chr,
            g.n_gwas_hits,
            g.best_gwas_p,
            g.mean_gwas_effect,
            COALESCE(d.n_de_genes, 0) AS n_de_genes,
            d.best_de_p,
            d.mean_abs_log2fc
        FROM gwas_signals g
        LEFT JOIN de_signals d ON g.chr = d.chr
        ORDER BY g.best_gwas_p ASC
        """
    )
    return (cross_ref,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 12. Integrated summary in R

    Send the cross-reference table back to R for a combined
    visualization showing GWAS and DE signal density per chromosome.
    """)
    return


@app.cell
def _(cross_ref, mo):
    _r_output = mo.r(
        """
    library(ggplot2)

    df <- as.data.frame(cross_ref)
    df$chr <- factor(df$chr, levels = df$chr[order(df$chr)])

    # Reshape for faceted comparison
    df_long <- data.frame(
      chr = rep(df$chr, 2),
      analysis = rep(c("GWAS hits", "DE genes"), each = nrow(df)),
      count = c(df$n_gwas_hits, df$n_de_genes),
      stringsAsFactors = FALSE
    )

    ggplot(df_long, aes(x = chr, y = count, fill = analysis)) +
      geom_col(position = "dodge", width = 0.7) +
      scale_fill_manual(
        values = c("GWAS hits" = "#d95f02", "DE genes" = "#7570b3"),
        name = NULL
      ) +
      labs(
        title = "Signal Density per Chromosome",
        subtitle = "GWAS significant variants vs differentially expressed genes",
        x = "Chromosome", y = "Count"
      ) +
      theme_minimal(base_size = 12) +
      theme(
        plot.title = element_text(face = "bold"),
        legend.position = "top"
      )
    """,
        inputs={"cross_ref": cross_ref},
        plot_format="svg",
        plot_width=900,
        plot_height=400,
    )
    return


if __name__ == "__main__":
    app.run()
