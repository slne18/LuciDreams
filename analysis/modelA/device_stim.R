#!/usr/bin/env Rscript

script_dir <- local({
  file_arg <- commandArgs(trailingOnly = FALSE)
  file_arg <- file_arg[grepl("^--file=", file_arg)]
  script_path <- if (length(file_arg) > 0) sub("^--file=", "", file_arg[1]) else "device_stim.R"
  normalizePath(dirname(script_path), winslash = "/", mustWork = FALSE)
})
source(file.path(script_dir, "..", "glmm_common.R"))

ctx <- init_glmm_script("device_stim.R")
input_file <- resolve_input_file(ctx$script_dir)
log_cat("Using input file:", input_file, "\n")

df <- read_cleaned_data(input_file)
df <- prepare_lucid_outcome(df)

binary_cols <- c(
  "cue_notice",
  "disruptive_arousal_any",
  "induction_arousal_any"
)

continuous_cols <- c(
  "rem_episode_count",
  "rem_minutes",
  "total_induction_cues",
  "rem_motion_avg"
)

factor_cols <- c("condition")

raw_predictor_cols <- c(binary_cols, continuous_cols, factor_cols)
model_df <- df[complete.cases(df[, c("lucid", "pid", raw_predictor_cols)]), , drop = FALSE]
model_df <- add_transformed_predictors(model_df, continuous_cols)

for (col in binary_cols) {
  model_df[[col]] <- factor(as.integer(model_df[[col]]), levels = c(0, 1))
  model_df[[col]] <- relevel(
    model_df[[col]],
    ref = names(sort(table(model_df[[col]]), decreasing = TRUE))[1]
  )
}

model_df$condition <- factor(as.integer(model_df$condition))
if (nlevels(model_df$condition) > 1) {
  model_df$condition <- relevel(
    model_df$condition,
    ref = names(sort(table(model_df$condition), decreasing = TRUE))[1]
  )
}

fixed_predictors <- c(
  binary_cols,
  log_z_predictor_suffix(continuous_cols),
  "condition"
)

log_cat("Rows before complete-case filtering:", nrow(df), "\n")
log_cat("Rows used in model:", nrow(model_df), "\n")
log_cat("Binary predictors (factors, reference = most common level):\n")
for (col in binary_cols) {
  log_cat(sprintf("  - %s (reference = %s)\n", col, levels(model_df[[col]])[1]))
}
print_predictor_preprocessing(continuous_cols)
log_cat(sprintf("Factor predictor: condition (reference = %s)\n", levels(model_df$condition)[1]))

run_collinearity_diagnostics(
  data = model_df,
  fixed_predictors = fixed_predictors,
  model_label = "device_stim",
  results_dir = ctx$results_dir,
  timestamp = ctx$timestamp
)

formula <- as.formula(
  paste(
    "lucid ~",
    paste(fixed_predictors, collapse = " + "),
    "+ (1 | pid)"
  )
)

run_lucid_glmm(
  formula = formula,
  data = model_df,
  model_label = "device_stim",
  script_name = ctx$script_name,
  results_dir = ctx$results_dir,
  timestamp = ctx$timestamp,
  summary_title = "Model A Summary — device stimulation (glmmTMB binomial)"
)
