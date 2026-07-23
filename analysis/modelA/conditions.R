#!/usr/bin/env Rscript

script_dir <- local({
  file_arg <- commandArgs(trailingOnly = FALSE)
  file_arg <- file_arg[grepl("^--file=", file_arg)]
  script_path <- if (length(file_arg) > 0) sub("^--file=", "", file_arg[1]) else "conditions.R"
  normalizePath(dirname(script_path), winslash = "/", mustWork = FALSE)
})
source(file.path(script_dir, "..", "glmm_common.R"))

ctx <- init_glmm_script("conditions.R")
input_file <- resolve_input_file(ctx$script_dir)
log_cat("Using input file:", input_file, "\n")

df <- read_cleaned_data(input_file)
df <- prepare_lucid_outcome(df)

raw_predictor_cols <- c("condition")
model_df <- df[stats::complete.cases(df[, c("lucid", "pid", raw_predictor_cols)]), , drop = FALSE]

model_df$condition <- factor(as.integer(model_df$condition))
if (nlevels(model_df$condition) < 2) {
  stop("Need at least two condition levels to fit lucid ~ condition.")
}
model_df$condition <- stats::relevel(
  model_df$condition,
  ref = names(sort(table(model_df$condition), decreasing = TRUE))[1]
)

log_cat("Rows before complete-case filtering:", nrow(df), "\n")
log_cat("Rows used in model:", nrow(model_df), "\n")
log_cat(sprintf("Predictor: condition (reference = %s)\n", levels(model_df$condition)[1]))
log_cat("Condition counts:\n")
print(table(model_df$condition))

formula <- stats::as.formula("lucid ~ condition + (1 | pid)")

run_lucid_glmm(
  formula = formula,
  data = model_df,
  model_label = "conditions",
  script_name = ctx$script_name,
  results_dir = ctx$results_dir,
  timestamp = ctx$timestamp,
  summary_title = "Model A Summary — condition only (glmmTMB binomial)"
)
