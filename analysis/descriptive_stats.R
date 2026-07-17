#!/usr/bin/env Rscript

script_dir <- local({
  file_arg <- commandArgs(trailingOnly = FALSE)
  file_arg <- file_arg[grepl("^--file=", file_arg)]
  script_path <- if (length(file_arg) > 0) sub("^--file=", "", file_arg[1]) else "descriptive_stats.R"
  normalizePath(dirname(script_path), winslash = "/", mustWork = FALSE)
})
source(file.path(script_dir, "glmm_common.R"))

ctx <- init_glmm_script("descriptive_stats.R")
input_file <- resolve_input_file(ctx$script_dir)

log_file <- file.path(ctx$results_dir, paste0("descriptive_stats_", ctx$timestamp, ".log"))
log_con <- file(log_file, open = "wt")
sink(log_con, split = TRUE)
sink(log_con, type = "message")
on.exit({
  sink(type = "message")
  sink()
  close(log_con)
}, add = TRUE)

cat("Saving output to:", log_file, "\n")
cat("Using input file:", input_file, "\n")

MORNING_SLEEP_ITEMS <- c(
  "You feel more restless than usual",
  "You woke up more than usual during last night",
  "Waking up in the morning was more difficult than usual",
  "It took longer than usual to wake up",
  "You felt more tired than usual when waking up"
)

DREAM_SUBSCALES <- c(
  "While dreaming, I was aware of the fact that the things I was experiencing in the dream were not real.",
  "While dreaming, I was aware that the self I experienced in my dream wasn't the same as my waking self.",
  "While dreaming, I was aware of the fact that the body I experienced in the dream did not correspond to my real sleeping body.",
  "I was very certain that the things I was experiencing in my dream wouldn't have any consequences on the real world.",
  "While dreaming, I often asked myself whether I was dreaming.",
  "While dreaming, I was aware of the fact that other dream characters in my dream were not real."
)

MODEL_A_NUMERIC <- c(
  "Age",
  "baseline_LD_freq_ord",
  "baseline_sleep_qual",
  "time_asleep",
  MORNING_SLEEP_ITEMS
)

MODEL_A_BINARY <- c("lucid_state")

MODEL_B_BINARY <- c(
  "cue_notice",
  "disruptive_arousal_any",
  "induction_arousal_any"
)

MODEL_B_NUMERIC <- c(
  "rem_episode_count",
  "rem_minutes",
  "total_induction_cues",
  "rem_motion_avg",
  "time_asleep"
)

MODEL_C_NUMERIC <- DREAM_SUBSCALES

ALL_NUMERIC <- unique(c(
  MODEL_A_NUMERIC,
  MODEL_B_NUMERIC,
  MODEL_C_NUMERIC
))

ALL_BINARY <- unique(c(MODEL_A_BINARY, MODEL_B_BINARY))

ALL_CATEGORICAL <- c("Gender", "condition")

summarise_numeric <- function(x, variable) {
  x <- suppressWarnings(as.numeric(x))
  x <- x[is.finite(x)]
  if (length(x) == 0) {
    return(data.frame(
      variable = variable,
      n = 0L,
      mean = NA_real_,
      sd = NA_real_,
      median = NA_real_,
      p25 = NA_real_,
      p75 = NA_real_,
      min = NA_real_,
      max = NA_real_,
      stringsAsFactors = FALSE
    ))
  }
  qs <- unname(stats::quantile(x, c(0.25, 0.5, 0.75), na.rm = TRUE))
  data.frame(
    variable = variable,
    n = length(x),
    mean = mean(x),
    sd = stats::sd(x),
    median = qs[2],
    p25 = qs[1],
    p75 = qs[3],
    min = min(x),
    max = max(x),
    stringsAsFactors = FALSE
  )
}

summarise_binary <- function(x, variable) {
  x <- suppressWarnings(as.integer(x))
  x <- x[!is.na(x)]
  n <- length(x)
  n1 <- sum(x == 1L)
  data.frame(
    variable = variable,
    n = n,
    n_yes = n1,
    pct_yes = if (n > 0) 100 * n1 / n else NA_real_,
    stringsAsFactors = FALSE
  )
}

summarise_categorical <- function(x, variable) {
  x <- as.character(x)
  x[is.na(x) | !nzchar(x)] <- "Missing/Unknown"
  tab <- sort(table(x), decreasing = TRUE)
  data.frame(
    variable = variable,
    level = names(tab),
    n = as.integer(tab),
    pct = 100 * as.numeric(tab) / sum(tab),
    stringsAsFactors = FALSE
  )
}

fmt_mean_sd <- function(x, digits = 2) {
  x <- suppressWarnings(as.numeric(x))
  x <- x[is.finite(x)]
  if (length(x) == 0) {
    return(NA_character_)
  }
  sprintf(
    paste0("%.", digits, "f (%.", digits, "f)"),
    mean(x),
    stats::sd(x)
  )
}

fmt_median_iqr <- function(x, digits = 2) {
  x <- suppressWarnings(as.numeric(x))
  x <- x[is.finite(x)]
  if (length(x) == 0) {
    return(NA_character_)
  }
  q <- unname(stats::quantile(x, c(0.25, 0.5, 0.75), na.rm = TRUE))
  sprintf(
    paste0("%.", digits, "f [%.", digits, "f, %.", digits, "f]"),
    q[2],
    q[1],
    q[3]
  )
}

fmt_n_pct <- function(n, denom, digits = 1) {
  if (denom <= 0) {
    return(sprintf("0 (0.%d%%)", digits))
  }
  sprintf(paste0("%d (%.", digits, "f%%)"), n, 100 * n / denom)
}

add_sample_row <- function(rows, characteristic, value, n = NA_integer_, statistic = "") {
  rbind(
    rows,
    data.frame(
      characteristic = characteristic,
      statistic = statistic,
      value = value,
      n = n,
      stringsAsFactors = FALSE
    )
  )
}

add_continuous_sample_rows <- function(rows, label, x, digits = 2) {
  x <- suppressWarnings(as.numeric(x))
  x <- x[is.finite(x)]
  n <- length(x)
  rows <- add_sample_row(rows, label, fmt_mean_sd(x, digits = digits), n, "mean (SD)")
  add_sample_row(rows, label, fmt_median_iqr(x, digits = digits), n, "median [IQR]")
}

build_sample_characteristics <- function(df) {
  pid_ok <- !is.na(df$pid) & nzchar(as.character(df$pid))
  participant_df <- df[pid_ok, , drop = FALSE]
  participant_df <- participant_df[order(participant_df$pid), , drop = FALSE]
  participant_df <- participant_df[!duplicated(participant_df$pid), , drop = FALSE]

  nights_per_participant <- as.numeric(table(df$pid[pid_ok]))
  participant_n <- length(nights_per_participant)
  total_nights <- sum(nights_per_participant)

  sample_rows <- data.frame(
    characteristic = character(),
    statistic = character(),
    value = character(),
    n = integer(),
    stringsAsFactors = FALSE
  )

  sample_rows <- add_sample_row(sample_rows, "Participants", as.integer(participant_n), participant_n, "n")
  sample_rows <- add_sample_row(sample_rows, "Nights recorded", as.integer(total_nights), total_nights, "n")
  sample_rows <- add_continuous_sample_rows(sample_rows, "Nights per participant", nights_per_participant)

  if ("Age" %in% names(participant_df)) {
    sample_rows <- add_continuous_sample_rows(sample_rows, "Age (years)", participant_df$Age)
  }
  if ("baseline_LD_freq_ord" %in% names(participant_df)) {
    sample_rows <- add_continuous_sample_rows(
      sample_rows,
      "Baseline lucid-dream frequency (ordinal)",
      participant_df$baseline_LD_freq_ord
    )
  }
  if ("baseline_sleep_qual" %in% names(participant_df)) {
    sample_rows <- add_continuous_sample_rows(
      sample_rows,
      "Baseline sleep quality (0-10)",
      participant_df$baseline_sleep_qual
    )
  }

  if ("Gender" %in% names(participant_df)) {
    gender_vals <- as.character(participant_df$Gender)
    gender_vals[is.na(gender_vals) | !nzchar(gender_vals)] <- "Missing/Unknown"
    gender_tab <- sort(table(gender_vals), decreasing = TRUE)
    gender_denom <- sum(gender_tab)
    for (level_name in names(gender_tab)) {
      sample_rows <- add_sample_row(
        sample_rows,
        "Sex",
        fmt_n_pct(as.integer(gender_tab[[level_name]]), gender_denom),
        gender_denom,
        level_name
      )
    }
  }

  lucid_vals <- suppressWarnings(as.integer(df$lucid_state[pid_ok]))
  lucid_ok <- lucid_vals %in% c(0L, 1L)
  if (any(lucid_ok)) {
    lucid_rate <- tapply(lucid_vals[lucid_ok], df$pid[pid_ok][lucid_ok], mean)
    sample_rows <- add_continuous_sample_rows(
      sample_rows,
      "Lucid dream rate (proportion of recorded nights)",
      as.numeric(lucid_rate)
    )
  }

  sample_rows
}

lucid_rate_by <- function(df, group_col, label) {
  out <- df
  out$lucid <- suppressWarnings(as.integer(out$lucid_state))
  out <- out[out$lucid %in% c(0L, 1L) & !is.na(out[[group_col]]), , drop = FALSE]
  if (nrow(out) == 0) {
    return(data.frame(group = character(), n = integer(), lucid_rate = numeric()))
  }
  agg <- stats::aggregate(lucid ~ out[[group_col]], data = out, FUN = mean)
  names(agg) <- c("group", "lucid_rate")
  n_tab <- as.data.frame(table(out[[group_col]]), stringsAsFactors = FALSE)
  names(n_tab) <- c("group", "n")
  merge(n_tab, agg, by = "group", all.x = TRUE, sort = TRUE)
}

raw_df <- read_cleaned_data(input_file)
missing_cols <- setdiff(
  c("pid", "lucid_state", ALL_NUMERIC, ALL_BINARY, ALL_CATEGORICAL),
  names(raw_df)
)
if (length(missing_cols) > 0) {
  stop("Missing expected columns in merged data: ", paste(missing_cols, collapse = ", "))
}

df <- raw_df

cat("\n===== Sample Characteristics (participant-level) =====\n")
sample_df <- build_sample_characteristics(df)
print(sample_df, row.names = FALSE)

numeric_stats <- do.call(
  rbind,
  lapply(ALL_NUMERIC, function(col) summarise_numeric(df[[col]], col))
)
numeric_stats$model_set <- vapply(numeric_stats$variable, function(v) {
  in_a <- v %in% MODEL_A_NUMERIC
  in_b <- v %in% MODEL_B_NUMERIC
  in_c <- v %in% MODEL_C_NUMERIC
  sets <- c(if (in_a) "A", if (in_b) "B", if (in_c) "C")
  if (length(sets) == 0) "other" else paste(sets, collapse = "/")
}, character(1))
median_sd <- stats::median(numeric_stats$sd[is.finite(numeric_stats$sd) & numeric_stats$sd > 0], na.rm = TRUE)
numeric_stats$sd_vs_median_sd <- if (is.finite(median_sd) && median_sd > 0) {
  numeric_stats$sd / median_sd
} else {
  NA_real_
}
numeric_stats$large_scale_flag <- ifelse(
  !is.na(numeric_stats$sd_vs_median_sd) & numeric_stats$sd_vs_median_sd >= 5,
  "YES",
  ""
)
numeric_stats <- numeric_stats[order(-numeric_stats$sd, numeric_stats$variable), , drop = FALSE]

cat("\n===== Numeric Descriptive Stats (Models A/B/C variables) =====\n")
print(numeric_stats, row.names = FALSE)

binary_stats <- do.call(
  rbind,
  lapply(ALL_BINARY, function(col) summarise_binary(df[[col]], col))
)
cat("\n===== Binary Variable Summaries =====\n")
print(binary_stats, row.names = FALSE)

cat("\n===== Categorical Summaries =====\n")
cat_df <- do.call(
  rbind,
  lapply(ALL_CATEGORICAL, function(col) summarise_categorical(df[[col]], col))
)
print(cat_df, row.names = FALSE)

model_b_df <- df[as.integer(df$condition) %in% 0:3, , drop = FALSE]
model_c_df <- df[as.integer(df$lucid_state) == 0 & as.integer(df$condition) %in% 4:5, , drop = FALSE]

cat("\n===== Model B subset (conditions 0-3, n =", nrow(model_b_df), ") =====\n")
if (nrow(model_b_df) > 0) {
  print(table(model_b_df$condition), useNA = "ifany")
  b_sleep <- do.call(
    rbind,
    lapply(MORNING_SLEEP_ITEMS, function(col) summarise_numeric(model_b_df[[col]], col))
  )
  print(b_sleep, row.names = FALSE)
}

cat("\n===== Model C subset (non-lucid, conditions 4-5, n =", nrow(model_c_df), ") =====\n")
if (nrow(model_c_df) > 0) {
  print(table(model_c_df$condition), useNA = "ifany")
  c_sub <- do.call(
    rbind,
    lapply(DREAM_SUBSCALES, function(col) summarise_numeric(model_c_df[[col]], col))
  )
  print(c_sub, row.names = FALSE)
}

total_rows <- nrow(df)
rows_cued <- sum(suppressWarnings(as.numeric(df$total_induction_cues)) > 0, na.rm = TRUE)
rows_cued_non_missing <- sum(!is.na(suppressWarnings(as.numeric(df$total_induction_cues))))
rows_cued_pct <- if (rows_cued_non_missing > 0) 100 * rows_cued / rows_cued_non_missing else NA_real_
rows_induction_arousal <- sum(suppressWarnings(as.integer(df$induction_arousal_any)) == 1L, na.rm = TRUE)
rows_induction_arousal_non_missing <- sum(!is.na(suppressWarnings(as.integer(df$induction_arousal_any))))
rows_induction_arousal_pct <- if (rows_induction_arousal_non_missing > 0) {
  100 * rows_induction_arousal / rows_induction_arousal_non_missing
} else {
  NA_real_
}

cat(
  "\nRows with total_induction_cues > 0:",
  rows_cued, "/", total_rows,
  if (is.finite(rows_cued_pct)) sprintf("(%.2f%% of rows with non-missing total_induction_cues)", rows_cued_pct) else "",
  "\n"
)
cat(
  "Rows with induction_arousal_any == 1:",
  rows_induction_arousal, "/", total_rows,
  if (is.finite(rows_induction_arousal_pct)) {
    sprintf("(%.2f%% of rows with non-missing induction_arousal_any)", rows_induction_arousal_pct)
  } else {
    ""
  },
  "\n"
)

cat("\n===== Lucid rate by cue_notice =====\n")
print(lucid_rate_by(df, "cue_notice", "cue_notice"))

cat("\n===== Lucid rate by disruptive_arousal_any =====\n")
print(lucid_rate_by(df, "disruptive_arousal_any", "disruptive_arousal_any"))

cat("\n===== Lucid rate by induction_arousal_any =====\n")
print(lucid_rate_by(df, "induction_arousal_any", "induction_arousal_any"))

cat("\n===== Lucid rate by condition =====\n")
print(lucid_rate_by(df, "condition", "condition"))

model_lucid_df <- df[
  suppressWarnings(as.integer(df$lucid_state)) %in% c(0L, 1L) &
    !is.na(suppressWarnings(as.integer(df$induction_arousal_any))),
  ,
  drop = FALSE
]
cued_lucid_df <- model_lucid_df[
  suppressWarnings(as.numeric(model_lucid_df$total_induction_cues)) > 0,
  ,
  drop = FALSE
]

cat("\n===== Lucid rate by induction_arousal_any (all lucid-eligible nights) =====\n")
if (nrow(model_lucid_df) == 0) {
  cat("No rows available.\n")
} else {
  print(lucid_rate_by(model_lucid_df, "induction_arousal_any", "induction_arousal_any"))
}

cat("\n===== Lucid rate by induction_arousal_any (cued nights only) =====\n")
if (nrow(cued_lucid_df) == 0) {
  cat("No cued-night rows available.\n")
} else {
  print(lucid_rate_by(cued_lucid_df, "induction_arousal_any", "induction_arousal_any"))
}

cat("\n===== Cross-tab: induction_arousal_any x cued night (total_induction_cues > 0) =====\n")
if (nrow(model_lucid_df) == 0) {
  cat("No rows available for cross-tab.\n")
} else {
  cued_flag <- ifelse(
    suppressWarnings(as.numeric(model_lucid_df$total_induction_cues)) > 0,
    1L,
    0L
  )
  print(table(model_lucid_df$induction_arousal_any, cued_flag))
}

sample_file <- file.path(ctx$results_dir, paste0("descriptive_stats_sample_", ctx$timestamp, ".csv"))
numeric_file <- file.path(ctx$results_dir, paste0("descriptive_stats_numeric_", ctx$timestamp, ".csv"))
binary_file <- file.path(ctx$results_dir, paste0("descriptive_stats_binary_", ctx$timestamp, ".csv"))
categorical_file <- file.path(ctx$results_dir, paste0("descriptive_stats_categorical_", ctx$timestamp, ".csv"))
model_b_file <- file.path(ctx$results_dir, paste0("descriptive_stats_model_b_subset_", ctx$timestamp, ".csv"))
model_c_file <- file.path(ctx$results_dir, paste0("descriptive_stats_model_c_subset_", ctx$timestamp, ".csv"))
lucid_cue_file <- file.path(ctx$results_dir, paste0("descriptive_stats_lucid_by_cue_", ctx$timestamp, ".csv"))
lucid_disrupt_file <- file.path(ctx$results_dir, paste0("descriptive_stats_lucid_by_disruptive_arousal_", ctx$timestamp, ".csv"))
lucid_induction_file <- file.path(ctx$results_dir, paste0("descriptive_stats_lucid_by_induction_arousal_", ctx$timestamp, ".csv"))
lucid_cond_file <- file.path(ctx$results_dir, paste0("descriptive_stats_lucid_by_condition_", ctx$timestamp, ".csv"))

utils::write.csv(sample_df, sample_file, row.names = FALSE)
utils::write.csv(numeric_stats, numeric_file, row.names = FALSE)
utils::write.csv(binary_stats, binary_file, row.names = FALSE)
utils::write.csv(cat_df, categorical_file, row.names = FALSE)
if (nrow(model_b_df) > 0) {
  utils::write.csv(b_sleep, model_b_file, row.names = FALSE)
}
if (nrow(model_c_df) > 0) {
  utils::write.csv(c_sub, model_c_file, row.names = FALSE)
}
utils::write.csv(lucid_rate_by(df, "cue_notice", "cue_notice"), lucid_cue_file, row.names = FALSE)
utils::write.csv(
  lucid_rate_by(df, "disruptive_arousal_any", "disruptive_arousal_any"),
  lucid_disrupt_file,
  row.names = FALSE
)
utils::write.csv(
  lucid_rate_by(df, "induction_arousal_any", "induction_arousal_any"),
  lucid_induction_file,
  row.names = FALSE
)
utils::write.csv(lucid_rate_by(df, "condition", "condition"), lucid_cond_file, row.names = FALSE)

cat("\nSaved:\n")
cat(" ", sample_file, "\n")
cat(" ", numeric_file, "\n")
cat(" ", binary_file, "\n")
cat(" ", categorical_file, "\n")
if (nrow(model_b_df) > 0) cat(" ", model_b_file, "\n")
if (nrow(model_c_df) > 0) cat(" ", model_c_file, "\n")
cat(" ", lucid_cue_file, "\n")
cat(" ", lucid_disrupt_file, "\n")
cat(" ", lucid_induction_file, "\n")
cat(" ", lucid_cond_file, "\n")
