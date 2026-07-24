"""Column names for raw dream narrative fields in merged_data.xlsx."""

DREAM_TEXT_COLUMNS = (
    "What do you remember from your dreams or other experiences during sleep? ( sequence of events, thoughts, sensations, feelings)",
    "If so, describe when and how you realized you were dreaming",
    "If so, describe how they appeared in your dream.",
    "Describe how you became lucid. Was it the result of a cue, was it spontaneous, or was it something else that made you lucid?",
    "Is there anything else you remember thinking, feeling, or experiencing during your sleep?",
)

DREAM_TEXT_LABELS = {
    DREAM_TEXT_COLUMNS[0]: "Main dream narrative",
    DREAM_TEXT_COLUMNS[1]: "When/how lucidity was recognized",
    DREAM_TEXT_COLUMNS[2]: "How cues appeared in the dream",
    DREAM_TEXT_COLUMNS[3]: "How lucidity began (cue vs spontaneous vs other)",
    DREAM_TEXT_COLUMNS[4]: "Other sleep thoughts, feelings, or experiences",
}

MERGE_KEY_COLUMNS = ("pid", "condition", "lucid_state", "cue_notice", "time_asleep")

# CSV export for batch LLM scoring (see export_dream_csv.py)
DREAM_TEXT_EXPORT_COLUMNS = ("row_id", "pid", *DREAM_TEXT_COLUMNS, "dream_text")
