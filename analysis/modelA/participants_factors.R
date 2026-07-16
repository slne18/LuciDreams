#!/usr/bin/env Rscript

script_dir <- local({
  file_arg <- commandArgs(trailingOnly = FALSE)
  file_arg <- file_arg[grepl("^--file=", file_arg)]
  script_path <- if (length(file_arg) > 0) sub("^--file=", "", file_arg[1]) else "participants_factors.R"
  normalizePath(dirname(script_path), winslash = "/", mustWork = FALSE)
})
source(file.path(script_dir, "..", "glmm_common.R"))

ctx <- init_glmm_script("participants_factors.R")
input_file <- resolve_input_file(ctx$script_dir)
log_cat("Using input file:", input_file, "\n")

df <- read_cleaned_data(input_file)
df <- prepare_lucid_outcome(df)

continuous_cols <- c(
  "Age",
  "baseline_LD_freq_ord",
  "baseline_sleep_qual",
  "You feel more restless than usual",
  "You woke up more than usual during last night",
  "Waking up in the morning was more difficult than usual",
  "It took longer than usual to wake up",
  "You felt more tired than usual when waking up",
  "time_asleep"
)

raw_predictor_cols <- c(continuous_cols, "Gender")
model_df <- df[complete.cases(df[, c("lucid", "pid", raw_predictor_cols)]), , drop = FALSE]
model_df <- add_transformed_predictors(model_df, continuous_cols)
model_df$Gender <- factor(model_df$Gender)
if (nlevels(model_df$Gender) > 1) {
  model_df$Gender <- relevel(
    model_df$Gender,
    ref = names(sort(table(model_df$Gender), decreasing = TRUE))[1]
  )
}

log_cat("Rows before complete-case filtering:", nrow(df), "\n")
log_cat("Rows used in model:", nrow(model_df), "\n")
log_cat("All continuous predictors: log1p then z-scored.\n")
print_predictor_preprocessing(continuous_cols)

formula <- as.formula(
  paste(
    "lucid ~",
    paste(c(log_z_predictor_suffix(continuous_cols), "Gender"), collapse = " + "),
    "+ (1 | pid)"
  )
)

run_lucid_glmm(
  formula = formula,
  data = model_df,
  model_label = "participants_factors",
  script_name = ctx$script_name,
  results_dir = ctx$results_dir,
  timestamp = ctx$timestamp,
  summary_title = "Model A Summary — participant factors (glmmTMB binomial)"
)
