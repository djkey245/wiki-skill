"""All tunable thresholds in one place.

Values are starting points; calibrated values
are recorded in references/methodology.md.
Later plans append their thresholds here.
"""
from __future__ import annotations

# Data window and history (data §1)
EARLIEST_MONTH = "2015-07"

# Cache (data §5, calibration R19)
PROVISIONAL_MONTHS = 2
PROVISIONAL_TTL_SECONDS = 2 * 24 * 3600
NOT_FOUND_TTL_SECONDS = 7 * 24 * 3600
METADATA_TTL_SECONDS = 7 * 24 * 3600
REVISION_TOLERANCE = 0.005

# Network (architecture §3, calibration R12)
AQS_PARALLELISM = 3  # Wikimedia APIs/Rate limits (2026): at most 3 concurrent requests
RATE_LIMIT_PER_MINUTE = 200  # unauthenticated clients with a compliant User-Agent
ACTION_API_BATCH = 50
REQUEST_TIMEOUT_SECONDS = 20.0
MAX_RETRIES = 4
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 30.0
MAXLAG_SECONDS = 5

# Deadline (data §4)
DEFAULT_MAX_SECONDS = 90

# Trend estimation (analysis §3–4, calibration R1, R18)
ALPHA = 0.05
MAX_VARIANCE_INFLATION = 4.0
MIN_HISTORY_FOR_PROFILE = 24

# Baskets and metrics (analysis §1–2, calibration R23)
ALIVE_MIN_VIEWS = 30
ALIVE_MAX_ZERO_RUN = 2
BASE_MONTHS = 12
LEVEL_MONTHS = 12
YOY_MONTHS = 6
MIN_WINDOW_FOR_YOY = 18

# Direction, checks and confidence (analysis §4–6, calibration R4, R6)
FLAT_PCT = 5.0
LOW_VOLUME_HIGH = 1000
LOW_VOLUME_BLOCKING = 100
MAX_ZERO_SHARE = 0.20
SPIKE_Z = 3.5
SPIKE_SLOPE_DROP = 0.5
CONCENTRATED_CONTRIBUTION = 0.60
SHORT_WINDOW_MONTHS = 24
THIN_BASKET_MAX = 3

# Wikimedia data layer (data §1–4, calibration R12)
HISTORY_BEFORE_WINDOW = 36
AVAILABILITY_PROBE_MONTHS = 3
MAX_REDIRECTS = 10
REDIRECT_RANK_DAYS = 60
PAGEVIEWS_TITLES_PER_REQUEST = 5
SEARCH_LIMIT = 10
MORELIKE_LIMIT = 20
MAX_LINK_PAGES = 3
MAX_REQUESTS = 600
MAX_SUGGEST_REQUESTS = 250

# Spec and limits (agent contract §2, architecture §3)
DEFAULT_PERIOD_MONTHS = 24
MIN_PERIOD_MONTHS = 12
MAX_QIDS_PER_TOPIC = 50
MAX_TOPICS = 5
MAX_LANGS = 10
SLUG_MAX_LENGTH = 40

# Topic search and candidates (data §3, calibration R9, R10, R17)
FIND_CANDIDATES = 5
DRAFT_CANDIDATES = 8
CANDIDATES_FOR_METADATA = 60  # top candidates by the cheap score that get Wikidata claims and a backlink check
SUGGEST_LIMIT = 30
CANDIDATE_WEIGHTS = {"morelike": 1.0, "link": 1.0, "backlink": 0.5, "wikidata": 1.0}
# Direct P31 values excluded from candidates (data §3); subclasses are not followed
EXCLUDED_TYPES = {
    "Q5": "human",
    "Q6256": "country",
    "Q3624078": "sovereign state",
    "Q515": "city",
    "Q1549591": "big city",
    "Q5119": "capital city",
    "Q577": "year",
    "Q3186692": "calendar year",
    "Q578": "century",
    "Q39911": "decade",
    "Q34770": "language",
    "Q1288568": "modern language",
    "Q4167410": "Wikimedia disambiguation page",
    "Q13406463": "Wikimedia list article",
}
DISAMBIGUATION = "Q4167410"

# Comparison, matrix and recommendations (recommendations §1–5, calibration R3, R13)
MIN_MATRIX_ITEMS = 3  # fewer items: a table without quadrants
EXPLORE_MAX = 3
MAX_REASONS_SHOWN = 5  # per language in stdout / answer_md
MAX_EXCLUDED_SHOWN = 10
MAX_DETAILED_CELLS = 9  # more topic x language cells: a group summary first, details only for key cells

# Report page (report part of the spec)
PDF_MAX_CHARTS = 3
PDF_TABLE_ROWS = 14
PDF_HEADLINE_LINES = 6
PDF_FONT_SIZES = (7.0, 6.5, 6.0, 5.5, 5.0)  # the text block shrinks before the recommendations are cut
PDF_LINE_SPACING = 1.3
PDF_WRAP_AT_7PT = 125  # characters per line of the text block at 7 pt; scaled for smaller fonts
PDF_TABLE_ROW_HEIGHT = 0.016  # of the page height
CHART_MAX_SERIES = 4  # coloured lines per chart; the rest are grey "others" (dataviz: no more hues)

# Slice-2 checks (analysis §5, data §6, calibration R4, R15)
BIC_STEP_MARGIN = 2.0  # step_like: BIC_step + margin < BIC_linear
STEP_MIN_SEGMENT = 6  # the break point runs from the 6th to the (n-6)th month of the window
RECENT_ONLY_MONTHS = 3
TREND_REVERSAL_PCT = 10.0  # |recent_yoy_pct| opposite to the trend category
REDIRECT_HEAVY_SHARE = 0.50
BREAK_SIDE_MONTHS = 6  # data_break: median of 6 months after minus 6 before
BREAK_SHIFT_FACTOR = 3.0  # data_break: shift above this many typical 6-month changes of the history
