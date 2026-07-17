# Shared helpers for LuciDreams GLMM scripts (glmmTMB binomial, lucid outcome).

suppressPackageStartupMessages({
  if (!requireNamespace("readxl", quietly = TRUE)) {
    stop("Install readxl: install.packages('readxl')")
  }
})

script_dir_from_args <- function() {
  file_arg <- commandArgs(trailingOnly = FALSE)
  file_arg <- file_arg[grepl("^--file=", file_arg)]
  script_path <- if (length(file_arg) > 0) sub("^--file=", "", file_arg[1]) else "."
  normalizePath(dirname(script_path), winslash = "/", mustWork = FALSE)
}

init_glmm_script <- function(script_name) {
  script_dir <- script_dir_from_args()
  timestamp <- format(Sys.time(), "%Y%m%d_%H%M%S")
  results_dir <- file.path(script_dir, "results")
  dir.create(results_dir, recursive = TRUE, showWarnings = FALSE)

  list(
    script_name = script_name,
    script_dir = script_dir,
    analysis_dir = normalizePath(file.path(script_dir, ".."), winslash = "/", mustWork = FALSE),
    repo_root = normalizePath(file.path(script_dir, "..", ".."), winslash = "/", mustWork = FALSE),
    results_dir = results_dir,
    timestamp = timestamp
  )
}

log_cat <- function(...) {
  cat(..., file = stderr())
}

resolve_input_file <- function(script_dir) {
  explicit <- Sys.getenv("LUCIDREAMS_MERGED_DATA", "")
  rel <- file.path("data_prep", "output", "analysis_data")
  base_candidates <- unique(normalizePath(c(
    script_dir,
    file.path(script_dir, ".."),
    file.path(script_dir, "..", ".."),
    file.path(script_dir, "..", "..", "..")
  ), winslash = "/", mustWork = FALSE))

  candidates <- c(
    explicit,
    unlist(lapply(base_candidates, function(root) {
      c(
        file.path(root, rel, "merged_data.xlsx"),
        file.path(root, rel, "merged_data.csv")
      )
    }), use.names = FALSE)
  )

  for (path in candidates) {
    if (nzchar(path) && file.exists(path)) {
      return(normalizePath(path, winslash = "/", mustWork = TRUE))
    }
  }

  stop(
    "Could not find merged analysis data. ",
    "Run data_prep/build_merged_data.py or set LUCIDREAMS_MERGED_DATA."
  )
}

read_cleaned_data <- function(path) {
  if (grepl("\\.xlsx$", path, ignore.case = TRUE)) {
    df <- as.data.frame(readxl::read_excel(path))
  } else {
    df <- utils::read.csv(path, stringsAsFactors = FALSE, check.names = FALSE)
  }

  if ("pid" %in% names(df)) {
    df$pid <- as.character(df$pid)
  }

  df
}

prepare_lucid_outcome <- function(df) {
  if (!"lucid_state" %in% names(df)) {
    stop("Missing lucid_state column in input data.")
  }

  df$lucid <- as.integer(df$lucid_state)
  df
}

zscore_predictor_name <- function(col) {
  paste0(make.names(col), "_z")
}

zscore_predictor_suffix <- function(cols) {
  vapply(cols, zscore_predictor_name, character(1), USE.NAMES = FALSE)
}

log_z_predictor_name <- function(col) {
  paste0(make.names(col), "_log_z")
}

log_z_predictor_suffix <- function(cols) {
  vapply(cols, log_z_predictor_name, character(1), USE.NAMES = FALSE)
}

zscore_vector <- function(x) {
  out <- rep(NA_real_, length(x))
  ok <- !is.na(x)
  if (!any(ok)) {
    return(out)
  }

  vals <- x[ok]
  mu <- mean(vals)
  sdv <- stats::sd(vals)
  if (is.na(sdv) || sdv == 0) {
    out[ok] <- 0
  } else {
    out[ok] <- (vals - mu) / sdv
  }

  out
}

add_zscored_predictors <- function(df, cols) {
  out <- df

  for (col in cols) {
    if (!col %in% names(out)) {
      stop(sprintf("Missing predictor column: %s", col))
    }

    z_col <- zscore_predictor_name(col)
    vals <- suppressWarnings(as.numeric(out[[col]]))
    out[[z_col]] <- zscore_vector(vals)
  }

  out
}

add_transformed_predictors <- function(df, continuous_cols) {
  out <- df

  for (col in continuous_cols) {
    if (!col %in% names(out)) {
      stop(sprintf("Missing predictor column: %s", col))
    }

    log_z_col <- log_z_predictor_name(col)
    vals <- suppressWarnings(as.numeric(out[[col]]))
    logged <- rep(NA_real_, length(vals))
    ok <- !is.na(vals)
    logged[ok] <- log1p(pmax(vals[ok], 0))
    out[[log_z_col]] <- zscore_vector(logged)
  }

  out
}

print_predictor_preprocessing <- function(continuous_cols) {
  log_cat("Continuous predictors (log1p, then z-scored as *_log_z):\n")
  for (col in continuous_cols) {
    log_cat(sprintf("  - %s -> %s\n", col, log_z_predictor_name(col)))
  }
}

run_lucid_glmm <- function(
  formula,
  data,
  model_label,
  script_name,
  results_dir,
  timestamp,
  summary_title
) {
  if (!requireNamespace("glmmTMB", quietly = TRUE)) {
    stop("Install glmmTMB: install.packages('glmmTMB')")
  }

  log_cat("Fitting:", deparse(formula), "\n")
  log_cat("Complete cases:", nrow(data), "\n")

  fit <- glmmTMB::glmmTMB(
    formula = formula,
    data = data,
    family = binomial()
  )

  summ <- summary(fit)
  log_cat("\n", summary_title, "\n", sep = "")
  print(summ)

  base <- file.path(results_dir, paste0(model_label, "_", timestamp))
  utils::capture.output(print(summ), file = paste0(base, "_summary.txt"))

  if (requireNamespace("broom.mixed", quietly = TRUE)) {
    coefs <- broom.mixed::tidy(fit, effects = "fixed", conf.int = TRUE)
    utils::write.csv(coefs, paste0(base, "_coefficients.csv"), row.names = FALSE)
  }

  invisible(fit)
}

run_glmm_model <- function(
  formula,
  data,
  family,
  model_label,
  results_dir,
  timestamp,
  summary_title
) {
  if (!requireNamespace("glmmTMB", quietly = TRUE)) {
    stop("Install glmmTMB: install.packages('glmmTMB')")
  }

  log_cat("Fitting:", deparse(formula), "\n")
  log_cat("Complete cases:", nrow(data), "\n")

  fit <- glmmTMB::glmmTMB(
    formula = formula,
    data = data,
    family = family
  )

  summ <- summary(fit)
  log_cat("\n", summary_title, "\n", sep = "")
  print(summ)

  base <- file.path(results_dir, paste0(model_label, "_", timestamp))
  utils::capture.output(print(summ), file = paste0(base, "_summary.txt"))

  coefs <- NULL
  if (requireNamespace("broom.mixed", quietly = TRUE)) {
    coefs <- broom.mixed::tidy(fit, effects = "fixed", conf.int = TRUE)
    utils::write.csv(coefs, paste0(base, "_coefficients.csv"), row.names = FALSE)
  }

  invisible(list(fit = fit, coefficients = coefs))
}

run_ordinal_clmm <- function(
  formula,
  data,
  model_label,
  results_dir,
  timestamp,
  summary_title
) {
  if (!requireNamespace("ordinal", quietly = TRUE)) {
    stop("Install ordinal: install.packages('ordinal')")
  }

  log_cat("Fitting:", deparse(formula), "\n")
  log_cat("Complete cases:", nrow(data), "\n")

  fit <- ordinal::clmm(formula, data = data, link = "logit")

  summ <- summary(fit)
  log_cat("\n", summary_title, "\n", sep = "")
  print(summ)

  base <- file.path(results_dir, paste0(model_label, "_", timestamp))
  utils::capture.output(print(summ), file = paste0(base, "_summary.txt"))

  coef_tab <- as.data.frame(stats::coef(summ))
  coef_tab$term <- rownames(coef_tab)
  rownames(coef_tab) <- NULL
  names(coef_tab)[1:4] <- c("estimate", "std.error", "z.value", "p.value")
  utils::write.csv(coef_tab, paste0(base, "_coefficients.csv"), row.names = FALSE)

  invisible(list(fit = fit, coefficients = coef_tab))
}

outcome_slug <- function(col) {
  make.names(col)
}

prepare_ordinal_outcome <- function(values) {
  ints <- suppressWarnings(as.integer(values))
  if (any(is.na(ints) & !is.na(values))) {
    stop("Outcome contains non-integer values for ordinal model.")
  }
  factor(ints, levels = 0:5, ordered = TRUE)
}

run_collinearity_diagnostics <- function(
  data,
  fixed_predictors,
  model_label,
  results_dir,
  timestamp
) {
  if (!requireNamespace("car", quietly = TRUE)) {
    stop("Install car for collinearity diagnostics: install.packages('car')")
  }

  missing <- setdiff(fixed_predictors, names(data))
  if (length(missing) > 0) {
    stop(sprintf("Missing predictor columns for collinearity check: %s", paste(missing, collapse = ", ")))
  }

  pred_df <- data[, fixed_predictors, drop = FALSE]
  base <- file.path(results_dir, paste0(model_label, "_", timestamp, "_collinearity"))
  report_lines <- c("Collinearity diagnostics", "====================", "")

  numeric_cols <- names(pred_df)[vapply(pred_df, is.numeric, logical(1))]
  if (length(numeric_cols) >= 2) {
    cor_mat <- stats::cor(pred_df[, numeric_cols, drop = FALSE], use = "pairwise.complete.obs")
    utils::write.csv(cor_mat, paste0(base, "_correlation.csv"))
    report_lines <- c(
      report_lines,
      "Pairwise correlations among numeric predictors:",
      capture.output(print(round(cor_mat, 3))),
      "",
      "Rule of thumb: |r| > 0.7 suggests strong pairwise association.",
      ""
    )

    high_cor <- which(abs(cor_mat) > 0.7 & abs(cor_mat) < 1, arr.ind = TRUE)
    if (nrow(high_cor) > 0) {
      report_lines <- c(report_lines, "Pairs with |r| > 0.7:")
      seen <- character(0)
      for (i in seq_len(nrow(high_cor))) {
        row_name <- rownames(cor_mat)[high_cor[i, 1]]
        col_name <- colnames(cor_mat)[high_cor[i, 2]]
        pair_key <- paste(sort(c(row_name, col_name)), collapse = "|")
        if (pair_key %in% seen) next
        seen <- c(seen, pair_key)
        report_lines <- c(
          report_lines,
          sprintf("  %s vs %s: r = %.3f", row_name, col_name, cor_mat[high_cor[i, 1], high_cor[i, 2]])
        )
      }
      report_lines <- c(report_lines, "")
    }
  }

  glm_formula <- stats::as.formula(
    paste("lucid ~", paste(fixed_predictors, collapse = " + "))
  )
  glm_fit <- stats::glm(glm_formula, data = data, family = stats::binomial())
  vif_vals <- car::vif(glm_fit)

  if (is.matrix(vif_vals)) {
    vif_df <- as.data.frame(vif_vals)
    vif_df$term <- rownames(vif_vals)
    rownames(vif_df) <- NULL
    utils::write.csv(vif_df, paste0(base, "_vif.csv"), row.names = FALSE)
    report_lines <- c(
      report_lines,
      "VIF / GVIF from fixed-effects GLM approximation:",
      capture.output(print(round(vif_vals, 3))),
      "",
      "Rule of thumb: GVIF^(1/(2*Df)) > 2 or VIF > 5 suggests problematic collinearity.",
      ""
    )
  } else {
    vif_df <- data.frame(term = names(vif_vals), vif = as.numeric(vif_vals))
    utils::write.csv(vif_df, paste0(base, "_vif.csv"), row.names = FALSE)
    report_lines <- c(
      report_lines,
      "VIF from fixed-effects GLM approximation:",
      capture.output(print(round(vif_vals, 3))),
      "",
      "Rule of thumb: VIF > 5 suggests problematic collinearity.",
      ""
    )

    high_vif <- vif_df$term[vif_df$vif > 5]
    if (length(high_vif) > 0) {
      report_lines <- c(
        report_lines,
        "Predictors with VIF > 5:",
        paste0("  - ", high_vif),
        ""
      )
    }
  }

  writeLines(report_lines, paste0(base, ".txt"))
  log_cat("\nCollinearity diagnostics written to ", base, "*\n", sep = "")
  invisible(vif_vals)
}
