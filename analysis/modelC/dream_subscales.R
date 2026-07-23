#!/usr/bin/env Rscript

script_dir <- local({
  file_arg <- commandArgs(trailingOnly = FALSE)
  file_arg <- file_arg[grepl("^--file=", file_arg)]
  script_path <- if (length(file_arg) > 0) sub("^--file=", "", file_arg[1]) else "dream_subscales.R"
  normalizePath(dirname(script_path), winslash = "/", mustWork = FALSE)
})
source(file.path(script_dir, "..", "glmm_common.R"))

ctx <- init_glmm_script("dream_subscales.R")
input_file <- resolve_input_file(ctx$script_dir)
log_cat("Using input file:", input_file, "\n")

df <- read_cleaned_data(input_file)

dream_subscale_outcomes <- c(
  "While dreaming, I was aware of the fact that the things I was experiencing in the dream were not real.",
  "While dreaming, I was aware that the self I experienced in my dream wasn't the same as my waking self.",
  "While dreaming, I was aware of the fact that the body I experienced in the dream did not correspond to my real sleeping body.",
  "I was very certain that the things I was experiencing in my dream wouldn't have any consequences on the real world.",
  "While dreaming, I often asked myself whether I was dreaming.",
  "While dreaming, I was aware of the fact that other dream characters in my dream were not real."
)

raw_cols <- c("condition", "pid", dream_subscale_outcomes)
model_df <- df[stats::complete.cases(df[, raw_cols]), , drop = FALSE]

model_df$condition <- factor(as.integer(model_df$condition))
if (nlevels(model_df$condition) < 2) {
  stop("Need at least two condition levels to fit subscale ~ condition.")
}
model_df$condition <- stats::relevel(
  model_df$condition,
  ref = names(sort(table(model_df$condition), decreasing = TRUE))[1]
)

log_cat("Rows before filtering:", nrow(df), "\n")
log_cat("Rows used in model C (all nights, all conditions):", nrow(model_df), "\n")
log_cat(sprintf("Condition reference level: %s\n", levels(model_df$condition)[1]))
log_cat("Condition counts:\n")
print(table(model_df$condition))

all_coefs <- list()

for (outcome_col in dream_subscale_outcomes) {
  slug <- outcome_slug(outcome_col)
  log_cat("\n=== Outcome:", outcome_col, "===\n")

  linear_formula <- stats::as.formula(
    paste0("`", outcome_col, "` ~ condition + (1 | pid)")
  )
  linear_result <- run_glmm_model(
    formula = linear_formula,
    data = model_df,
    family = stats::gaussian(),
    model_label = paste0("dream_subscales_", slug, "_linear"),
    results_dir = ctx$results_dir,
    timestamp = ctx$timestamp,
    summary_title = paste0(
      "Model C (linear) — ", outcome_col,
      " ~ condition + (1 | pid); all nights; reference = ",
      levels(model_df$condition)[1]
    )
  )

  ordinal_col <- paste0(slug, "_ord")
  ordinal_df <- model_df
  ordinal_df[[ordinal_col]] <- prepare_ordinal_outcome(ordinal_df[[outcome_col]])
  ordinal_formula <- stats::as.formula(
    paste0(ordinal_col, " ~ condition + (1 | pid)")
  )
  ordinal_result <- run_ordinal_clmm(
    formula = ordinal_formula,
    data = ordinal_df,
    model_label = paste0("dream_subscales_", slug, "_ordinal"),
    results_dir = ctx$results_dir,
    timestamp = ctx$timestamp,
    summary_title = paste0(
      "Model C (ordinal CLMM) — ", outcome_col,
      " ~ condition + (1 | pid); all nights; reference = ",
      levels(model_df$condition)[1]
    )
  )

  if (!is.null(linear_result$coefficients)) {
    linear_coefs <- linear_result$coefficients
    linear_coefs <- linear_coefs[grepl("^condition", linear_coefs$term), , drop = FALSE]
    linear_coefs$outcome <- outcome_col
    linear_coefs$model_type <- "linear"
    all_coefs[[paste0(slug, "_linear")]] <- linear_coefs[, c("term", "estimate", "std.error", "statistic", "p.value", "outcome", "model_type"), drop = FALSE]
  }

  if (!is.null(ordinal_result$coefficients)) {
    ordinal_coefs <- ordinal_result$coefficients
    ordinal_coefs <- ordinal_coefs[grepl("^condition", ordinal_coefs$term), , drop = FALSE]
    ordinal_coefs$statistic <- ordinal_coefs$z.value
    ordinal_coefs$outcome <- outcome_col
    ordinal_coefs$model_type <- "ordinal"
    all_coefs[[paste0(slug, "_ordinal")]] <- ordinal_coefs[, c("term", "estimate", "std.error", "statistic", "p.value", "outcome", "model_type"), drop = FALSE]
  }
}

if (length(all_coefs) > 0) {
  combined <- do.call(rbind, all_coefs)
  combined_path <- file.path(
    ctx$results_dir,
    paste0("dream_subscales_all_coefficients_", ctx$timestamp, ".csv")
  )
  utils::write.csv(combined, combined_path, row.names = FALSE)
  log_cat("\nCombined coefficients written to ", combined_path, "\n", sep = "")
}
