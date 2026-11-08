"""Build docs/training-agent-mcp.pdf: a four-page design overview.

The PDF libraries are fetched on the fly and are not project dependencies:
    uv run --with reportlab python docs/build_pdf.py
The script fails if the result is not exactly four pages.
"""

import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

TARGET = Path(__file__).parent / "training-agent-mcp.pdf"
EXPECTED_PAGES = 4
INK = colors.HexColor("#1b1f24")
MUTED = colors.HexColor("#57606a")
RULE = colors.HexColor("#d0d7de")
ACCENT = colors.HexColor("#0b4f6c")
PANEL = colors.HexColor("#f6f8fa")
CARD = colors.white

BODY = ParagraphStyle(
    "body", fontName="Helvetica", fontSize=10, leading=14.2, textColor=INK, spaceAfter=7
)
TITLE = ParagraphStyle(
    "title", fontName="Helvetica-Bold", fontSize=21, leading=25, textColor=INK, spaceAfter=4
)
LEDE = ParagraphStyle(
    "lede", parent=BODY, fontSize=11.5, leading=16, textColor=MUTED, spaceAfter=12
)
H1 = ParagraphStyle(
    "h1",
    fontName="Helvetica-Bold",
    fontSize=13,
    leading=17,
    textColor=ACCENT,
    spaceBefore=9,
    spaceAfter=5,
)
H2 = ParagraphStyle(
    "h2",
    fontName="Helvetica-Bold",
    fontSize=10,
    leading=14,
    textColor=INK,
    spaceBefore=4,
    spaceAfter=2,
)
BULLET = ParagraphStyle("bullet", parent=BODY, leftIndent=12, bulletIndent=1, spaceAfter=4)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=9, leading=12, spaceAfter=0)
CELL_HEAD = ParagraphStyle("cell_head", parent=CELL, fontName="Helvetica-Bold")
CAPTION = ParagraphStyle(
    "caption", parent=BODY, fontSize=8.5, leading=11, textColor=MUTED, alignment=TA_CENTER
)
CODE = ParagraphStyle(
    "code",
    fontName="Courier",
    fontSize=8.2,
    leading=10.8,
    textColor=INK,
)


class ArchitectureDiagram(Flowable):
    """The same structure as the Mermaid diagram in the README, drawn for print."""

    def __init__(self, width: float, height: float = 306) -> None:
        super().__init__()
        self.width = width
        self.height = height

    def wrap(self, *_args: object) -> tuple[float, float]:
        return self.width, self.height

    def _group(
        self, x: float, y: float, w: float, h: float, label: str, label_at_bottom: bool = False
    ) -> None:
        c: Canvas = self.canv
        c.setStrokeColor(RULE)
        c.setFillColor(PANEL)
        c.setLineWidth(0.8)
        c.roundRect(x, y, w, h, 6, stroke=1, fill=1)
        c.setFillColor(MUTED)
        c.setFont("Helvetica-Bold", 8)
        c.drawString(x + 8, y + 7 if label_at_bottom else y + h - 12, label)

    def _box(
        self,
        cx: float,
        cy: float,
        w: float,
        title: str,
        note: str | None = None,
        h: float = 34,
        radius: float = 4,
        mono: bool = False,
    ) -> None:
        c: Canvas = self.canv
        c.setStrokeColor(ACCENT)
        c.setFillColor(CARD)
        c.setLineWidth(1)
        c.roundRect(cx - w / 2, cy - h / 2, w, h, radius, stroke=1, fill=1)
        c.setFillColor(INK)
        c.setFont("Courier-Bold" if mono else "Helvetica-Bold", 8.6)
        if note:
            c.drawCentredString(cx, cy + 2.5, title)
            c.setFont("Helvetica", 7.8)
            c.setFillColor(MUTED)
            c.drawCentredString(cx, cy - 9, note)
        else:
            c.drawCentredString(cx, cy - 3, title)

    def _arrow(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        both: bool = False,
    ) -> None:
        c: Canvas = self.canv
        c.setStrokeColor(MUTED)
        c.setFillColor(MUTED)
        c.setLineWidth(1)
        c.line(x1, y1, x2, y2)
        self._head(x1, y1, x2, y2)
        if both:
            self._head(x2, y2, x1, y1)

    def _head(self, x1: float, y1: float, x2: float, y2: float) -> None:
        c: Canvas = self.canv
        size = 4.5
        length = max(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5, 0.001)
        ux, uy = (x2 - x1) / length, (y2 - y1) / length
        px, py = -uy, ux
        path = c.beginPath()
        path.moveTo(x2, y2)
        path.lineTo(x2 - ux * size * 1.6 + px * size, y2 - uy * size * 1.6 + py * size)
        path.lineTo(x2 - ux * size * 1.6 - px * size, y2 - uy * size * 1.6 - py * size)
        path.close()
        c.drawPath(path, stroke=0, fill=1)

    def _label(self, x: float, y: float, text: str) -> None:
        c: Canvas = self.canv
        c.setFont("Helvetica-Oblique", 7.8)
        c.setFillColor(MUTED)
        c.drawCentredString(x, y, text)

    def draw(self) -> None:
        left_w = self.width - 112
        mid = left_w / 2
        analyst_x, planner_x = mid - 97, mid + 97

        self._group(0, 250, left_w, 54, "ENTRY POINTS")
        self._group(0, 166, left_w, 62, "AGENTS, DEFINED IN .claude/agents", label_at_bottom=True)
        self._group(0, 42, left_w, 94, "MCP SERVER, READ-ONLY")

        self._box(analyst_x, 270, 142, "/week", "Claude Code command", h=30)
        self._box(planner_x, 270, 142, "coach-week, run_evals.py", "PydanticAI runner", h=30)
        entry_bus_y = 239
        c: Canvas = self.canv
        c.setStrokeColor(MUTED)
        c.setLineWidth(1)
        c.line(planner_x, 255, planner_x, entry_bus_y)
        c.line(planner_x, entry_bus_y, analyst_x, entry_bus_y)
        self._arrow(analyst_x, 255, analyst_x, 216)

        self._box(analyst_x, 199, 132, "analyst (Haiku)", "reports what happened")
        self._box(planner_x, 199, 132, "planner (Sonnet)", "plans the next week")
        self._arrow(analyst_x + 66, 199, planner_x - 66, 199)
        self._label(mid, 203, "validated JSON")

        cx_model = self.width - 44
        self._box(cx_model, 197, 88, "Claude API", "models", h=44, radius=16)
        self._arrow(left_w + 1, 197, cx_model - 44, 197, both=True)

        self._arrow(mid, 166, mid, 136)
        self._label(mid + 40, 148, "MCP tool calls")

        tool_y = 108
        load_x, activities_x, goal_x = mid - 124, mid - 8, mid + 116
        self._box(load_x, tool_y, 106, "get_weekly_load", h=24, mono=True)
        self._box(activities_x, tool_y, 104, "get_activities", h=24, mono=True)
        self._box(goal_x, tool_y, 104, "coach://goal", h=24, mono=True)

        analytics_x, sources_x = mid - 90, mid + 62
        bus_y = 86
        c.setStrokeColor(MUTED)
        c.setLineWidth(1)
        c.line(load_x, tool_y - 12, load_x, bus_y)
        c.line(activities_x, tool_y - 12, activities_x, bus_y)
        c.line(load_x, bus_y, sources_x, bus_y)
        self._arrow(analytics_x, bus_y, analytics_x, 77)
        self._arrow(sources_x, bus_y, sources_x, 77)

        self._box(analytics_x, 62, 136, "analytics.py", "totals, flags, km ceiling", h=30)
        self._box(sources_x, 62, 136, "sources.py", "reads and validates activities", h=30)

        self._arrow(sources_x, 47, sources_x, 28)
        self._box(sources_x, 14, 190, "Intervals.icu API or JSON fixtures", h=26, radius=9)


def bullets(items: list[str]) -> list[Paragraph]:
    return [Paragraph(item, BULLET, bulletText="-") for item in items]


def table(rows: list[list[str]], widths: list[float]) -> Table:
    cells = [
        [Paragraph(cell, CELL_HEAD if i == 0 else CELL) for cell in row]
        for i, row in enumerate(rows)
    ]
    result = Table(cells, colWidths=widths, repeatRows=1)
    result.setStyle(
        TableStyle(
            [
                ("LINEBELOW", (0, 0), (-1, 0), 0.9, INK),
                ("LINEBELOW", (0, 1), (-1, -1), 0.4, RULE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return result


def code_block(source: str) -> Table:
    block = Table([[Preformatted(source, CODE)]], colWidths=[None])
    block.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PANEL),
                ("BOX", (0, 0), (-1, -1), 0.5, RULE),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return block


def footer(canvas: Canvas, doc: SimpleDocTemplate) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(doc.leftMargin, 1.15 * cm, "training-agent-mcp")
    canvas.drawRightString(A4[0] - doc.rightMargin, 1.15 * cm, f"{doc.page} / {EXPECTED_PAGES}")
    canvas.restoreState()


TRACE = """\
week 2026-W30                 15.75K tokens in, 885 out, 9.78 s
  agent analyst               10.58K in, 422 out, 4.39 s
    MCP connect               425 ms
    chat claude-haiku-5-5     2998 in,  87 out, 1.45 s
    tool get_weekly_load      29 ms
    chat claude-haiku-5-5     3544 in,  86 out, 958 ms
    tool get_activities       13 ms
    chat claude-haiku-5-5     4039 in, 249 out, 1.50 s
  agent planner               5170 in, 463 out, 5.39 s
    MCP connect               435 ms
    chat claude-sonnet-5-5    2504 in,  89 out, 1.80 s
    tool ReadMcpResourceTool  7 ms
    chat claude-sonnet-5-5    2666 in, 374 out, 3.13 s"""


def story(width: float) -> list[Flowable]:
    third = width / 3
    return [
        # ---- page 1 -----------------------------------------------------------------
        Paragraph("training-agent-mcp", TITLE),
        Paragraph(
            "A read-only MCP server over my running data, with two agents that review a "
            "training week and plan the next one.",
            LEDE,
        ),
        Paragraph("The problem", H1),
        Paragraph(
            "I worked on an enterprise MCP server with over a hundred tools and wanted to try "
            "the opposite: a small server with a couple of tools. This project answers two "
            "questions from my training data: how was my week, and what should next week look "
            "like?",
            BODY,
        ),
        Paragraph(
            "I set the requirements, made the design calls and ran the evals. Claude Code "
            "generated most of the code and wrote the eval cases.",
            BODY,
        ),
        Paragraph("Architecture", H1),
        ArchitectureDiagram(width),
        Spacer(1, 6),
        Paragraph(
            "Two entry points run the same agents. Both agents reach the data only through the "
            "server, and every number they see is computed there.",
            CAPTION,
        ),
        Paragraph("How one run works", H1),
        Paragraph(
            "Given an ISO week, the analyst calls the two tools and returns a JSON object with a "
            "fixed set of fields. The runner validates it with Pydantic and retries once if it "
            "does not match. The planner receives that JSON, reads the target race and returns "
            "seven days plus its reasoning, also validated. A run on the synthetic data takes "
            "about 13 seconds.",
            BODY,
        ),
        PageBreak(),
        # ---- page 2 -----------------------------------------------------------------
        Paragraph("Code does the arithmetic", H1),
        Paragraph(
            "Weekly totals, the percentage change, the zero-week streak, the flags and the ceiling "
            "for next week's distance are pure functions in "
            "<font face='Courier'>analytics.py</font>. "
            "The models copy these values and interpret them. The one flag a model decides, "
            "<font face='Courier'>race_detected</font>, travels in a separate field so it is "
            "always marked as an inference.",
            BODY,
        ),
        table(
            [
                ["Situation", "What the server returns", "What the planner must do"],
                [
                    "Normal week",
                    "Ceiling: the larger of the recent average and this week, plus 10 %",
                    "Stay at or below the ceiling",
                ],
                [
                    "Load spike or layoff",
                    "Ceiling: the recent average, with no increase",
                    "Plan recovery, or ask why the runner stopped",
                ],
                [
                    "Unreadable activities",
                    "No ceiling, and a fixed 10 km safety cap",
                    "Say the load is unknown and plan easy runs under the cap",
                ],
            ],
            [third * 0.8, third * 1.2, third],
        ),
        Spacer(1, 6),
        Paragraph("Design decisions", H1),
        Paragraph("Two tools", H2),
        Paragraph(
            "I decided this up front, as a reaction to the large server. "
            "<font face='Courier'>get_weekly_load</font> answers most questions and "
            "<font face='Courier'>get_activities</font> inspects specific runs. The race goal is "
            "static data, so it is an MCP resource. I did not measure this against a larger tool "
            "set, so it is a design choice and not a benchmark result.",
            BODY,
        ),
        Paragraph("Two agents", H2),
        Paragraph(
            "Separate agents keep raw tool output out of the planner's context and let extraction "
            "run on a cheaper model. In a typical run the analyst costs about $0.001 and the "
            "planner about $0.02.",
            BODY,
        ),
        Paragraph("One definition per agent", H2),
        Paragraph(
            "The Claude Code command and the Python runner read the same files for prompt, model "
            "and tool allowlist, so they cannot drift apart. The runner replaces only the "
            "planner's output format, because it needs validated fields.",
            BODY,
        ),
        Paragraph("Read-only, with least privilege", H2),
        Paragraph(
            "The server only issues GET requests. Each agent gets an explicit tool allowlist. In "
            "Claude Code, a hook also limits how much activity data one call can pull into "
            "context.",
            BODY,
        ),
        Paragraph("Errors say how to fix the call", H2),
        Paragraph(
            "A range that is too long, a bad week format, a rate limit or a missing goal file each "
            "return a message the agent can act on. A rate limit passes on the wait from the "
            "server, and only if it is a number, because that header is remote text that reaches "
            "the model.",
            BODY,
        ),
        Paragraph("Missing data stays explicit", H2),
        Paragraph(
            "An activity that exists but cannot be read is reported as blocked. It never counts as "
            "a rest day, it stops the percentage change and the ceiling from being computed, and "
            "it breaks the zero-week streak. Heart rate stays null when there is no sensor and is "
            "never estimated.",
            BODY,
        ),
        Paragraph("Stack", H2),
        Paragraph(
            "Python, Pydantic, the MCP Python SDK, PydanticAI and Logfire are the tools I work "
            "with. I left out LangChain because native tool calling and structured output already "
            "cover what this project needs. The code passes "
            "<font face='Courier'>mypy --strict</font> "
            "and has 140 offline tests.",
            BODY,
        ),
        PageBreak(),
        # ---- page 3 -----------------------------------------------------------------
        Paragraph("Evals", H1),
        Paragraph(
            "Each case runs the real agents and the real MCP server on its own data, and scores "
            "the validated output. A case holds the data, the question, the expected behaviour and "
            "a list of criteria, all under a Pydantic schema. A dry run compares expected values "
            "with what the server computes, without calling a model.",
            BODY,
        ),
        table(
            [
                ["Check", "When to use it", "Example"],
                [
                    "Code",
                    "Whenever the answer can be written as a comparison. It is free and "
                    "repeatable, and wording cannot fool it.",
                    "Flags are exactly <font face='Courier'>[\"layoff\"]</font>. "
                    "The plan totals 1 to 6 km.",
                ],
                [
                    "LLM judge",
                    "Only for meaning that code cannot decide. It runs after the code checks "
                    "pass, because it costs money and can be wrong.",
                    "The question asks why the runner stopped.",
                ],
            ],
            [third * 0.5, third * 1.5, third],
        ),
        Spacer(1, 5),
        Paragraph(
            "Failures of the plumbing, such as a rate limit or a timeout, count as errors and stay "
            "out of the pass rate. Plan totals have a lower limit as well as an upper one, "
            "otherwise an empty plan would pass.",
            BODY,
        ),
        Paragraph("Results", H1),
        table(
            [
                ["Set", "Cases", "Attempts", "Passed", "Cost"],
                ["Synthetic, in this repo", "5", "15", "15", "$0.33"],
                ["My real data, kept out of the repo", "6", "6", "6", "$0.15"],
            ],
            [third * 1.6, third * 0.35, third * 0.35, third * 0.35, third * 0.35],
        ),
        Spacer(1, 3),
        Paragraph(
            "Measured on 2026-11-08. The cases cover a normal week, a layoff, a week exactly at "
            "the spike threshold, a race close by, a race after low volume and several kinds of "
            "unreadable data.",
            CAPTION,
        ),
        Paragraph("What the evals caught", H1),
        Paragraph("The earlier runs failed three times, for different reasons.", BODY),
        *bullets(
            [
                "The planner had to plan below last week's distance, and "
                "with unreadable data that distance was 0 km. It said so and invented 9 to 13 km. "
                "The fix was in code: the server now returns a 10 km safety cap.",
                "One judge rubric rejected a 0.0 km field that the output "
                "contract requires, although the agent had described the data correctly. The "
                "rubric was rewritten after that failure, which is why the change is noted here.",
                "A Haiku judge failed a correct answer that a person "
                "would pass. This was measured on saved answers, so the agents did not vary.",
            ]
        ),
        Spacer(1, 3),
        table(
            [
                ["Saved answer, 10 trials each", "Right verdict", "Haiku", "Sonnet"],
                ["Correct answer, original case text", "Pass", "10/10", "10/10"],
                ["Correct answer, corrected case text", "Pass", "7/10", "10/10"],
                ["Deliberately bad answer", "Fail", "10/10", "10/10"],
            ],
            [third * 1.6, third * 0.5, third * 0.45, third * 0.45],
        ),
        Spacer(1, 3),
        Paragraph(
            "The judge now uses Sonnet, at about $0.004 per verdict against $0.0002. This used "
            "two saved answers, enough to choose a model but not to call Sonnet reliable.",
            CAPTION,
        ),
        PageBreak(),
        # ---- page 4 -----------------------------------------------------------------
        Paragraph("Observability", H1),
        Paragraph(
            "Logfire records every model request and tool call as one trace, including the steps "
            "inside the MCP server process. This is a real run, shortened:",
            BODY,
        ),
        code_block(TRACE),
        Spacer(1, 7),
        Paragraph(
            "Most of the time goes to the models, and tools take milliseconds. Starting a server "
            "process costs about 430 ms per agent. The run cost $0.016, computed from real token "
            "counts and a price table, one request at a time because some prices depend on prompt "
            "length.",
            BODY,
        ),
        Paragraph(
            "By default traces carry tokens, latency and cost, and never prompts or responses. A "
            "test searches the exported spans for known content. "
            "<font face='Courier'>COACH_LOG_CONTENT=true</font> writes that content to a local, "
            "git-ignored file for debugging and sends nothing out.",
            BODY,
        ),
        Paragraph("Limitations", H1),
        *bullets(
            [
                "Intervals.icu does not expose Strava-synced "
                "activities through its API. All 52 activities on my account since August 2025 "
                "come back with a date and no distance, so with live data the system can only "
                "report that data is missing. The real eval cases use six runs I transcribed by "
                "hand and those unreadable activities.",
                "The eval set is small and the cases share one author. Real cases ran once and "
                "synthetic cases three times. The results show the system follows its "
                "specification on these cases, not that the plans are good training advice.",
                "The planner tends to fill its limit. With a 6 km ceiling it plans 6 km. There is "
                "no taper rule, so the plan for a race 13 days away ranged from 32 to 40 km "
                "between runs, and all of those attempts passed.",
                "The 10 km cap is a cautious project choice, not a validated training rule.",
                "Cost figures are estimates. They come from token counts and a price table "
                "that does not update itself.",
            ]
        ),
        KeepTogether(
            [
                Paragraph("Run it", H1),
                code_block(
                    "uv sync\n"
                    "uv run pytest\n"
                    "ANTHROPIC_API_KEY=your-key uv run python evals/run_evals.py"
                ),
                Spacer(1, 7),
                Paragraph(
                    "The first two commands need no account. The third costs about $0.11 per pass. "
                    "Not medical or coaching advice.",
                    BODY,
                ),
            ]
        ),
    ]


def main() -> int:
    doc = SimpleDocTemplate(
        str(TARGET),
        pagesize=A4,
        leftMargin=2.1 * cm,
        rightMargin=2.1 * cm,
        topMargin=1.9 * cm,
        bottomMargin=2.0 * cm,
        title="training-agent-mcp: design overview",
        author="Miguel",
        subject="A small read-only MCP server with two agents and field-based evals",
    )
    doc.build(story(doc.width), onFirstPage=footer, onLaterPages=footer)
    print(f"{TARGET.name}: {doc.page} pages")
    if doc.page != EXPECTED_PAGES:
        print(f"FAIL: expected {EXPECTED_PAGES} pages.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
