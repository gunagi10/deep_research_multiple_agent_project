import re
import io
import asyncio
import streamlit as st
from typing import List, Optional, Tuple
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from agents import Runner

# Your orchestrator + models
import coordinator as coord_mod
from models import SearchResult

# Chat-first validator (calls the tool when ready)
from research_agents.intention_validator_agent import intention_validator_agent

# =============================================================================
# App-wide setup and constants
# =============================================================================
load_dotenv(override=True)
# Keep centered layout; small pages look nicer
st.set_page_config(page_title="Streamlit Deep Research", layout="centered")

st.title("🔎 Streamlit Deep Research")

# Default user timezone; can be overridden in session_state elsewhere
_DEFAULT_TZ = "America/Vancouver"

# Precompile ANSI regex once for speed and clarity
ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")


# =============================================================================
# Reset-after-download helpers
# =============================================================================
# We want a clean app after a download; so we arm a flag and rerun on next turn.
if st.session_state.get("_RESET_AFTER_DOWNLOAD_", False):
    # Clear all state; perform a fresh rerun
    st.session_state.clear()
    st.rerun()


def _arm_reset_after_download() -> None:
    """Arm the reset flag; the next rerun will start from a fresh state."""
    st.session_state["_RESET_AFTER_DOWNLOAD_"] = True


# =============================================================================
# Utility: ANSI cleanup for nicer Streamlit rendering
# =============================================================================

def strip_ansi(s: str) -> str:
    """Remove ANSI escape sequences and carriage returns from a string."""
    return ANSI_ESCAPE_RE.sub('', s).replace('\r', '')


# =============================================================================
# Stream collector for live logs and lightweight "thinking" snippets
# =============================================================================
class StreamlitRedirector(io.TextIOBase):
    """
    Collect logs and short "thinking" notes, then render them live.

    Contract:
      - Use .write() with text; returns number of characters written.
      - Thoughts or reasoning lines begin with 'Thoughts:' or 'Reasoning:'.
    """
    def __init__(self, log_placeholder, thinking_placeholder) -> None:
        super().__init__()
        self.log_placeholder = log_placeholder
        self.thinking_placeholder = thinking_placeholder
        self._buffer: List[str] = []          # raw log lines as received
        self._thoughts: List[str] = []         # unique thought snippets

    def write(self, s: str) -> int:  # type: ignore[override]
        if not isinstance(s, str):
            s = s.decode('utf-8', errors='ignore')
        if not s:
            return 0

        # Accumulate and show the raw log in a fenced block
        self._buffer.append(s)
        text = strip_ansi(''.join(self._buffer))
        self.log_placeholder.markdown(f"""```text{text}```""")

        # Extract and show simple thinking lines
        for line in s.splitlines():
            line_clean = strip_ansi(line).strip()
            head = line_clean.lower()
            if head.startswith('thoughts:') or head.startswith('reasoning:'):
                parts = line_clean.split(':', 1)
                if len(parts) == 2:
                    content = parts[1].strip()
                    if content and content not in self._thoughts:
                        self._thoughts.append(content)
                        md = "\n".join([f"- *{t}*" for t in self._thoughts])
                        self.thinking_placeholder.markdown(md)

        return len(s)

    def flush(self) -> None:  # match io.TextIOBase interface
        return None


# =============================================================================
# Async runner helper for Streamlit
# =============================================================================

def run_async(coro):
    """Safely run an async coroutine in Streamlit contexts."""
    try:
        return asyncio.run(coro)
    except RuntimeError:
        # Fall back to a fresh loop when one is already running
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(coro)
        finally:
            loop.close()


# =============================================================================
# UI: sources renderer
# =============================================================================

def render_sources(results: Optional[List[SearchResult]]) -> None:
    """Render numbered sources with title, URL, and summary."""
    if not results:
        return
    st.subheader("4) Sources")
    for i, r in enumerate(results, start=1):
        with st.expander(f"{i}. {r.title}", expanded=False):
            st.markdown(f"**[{i}] {r.title}**")
            st.write(f"**URL:** {r.url}")
            st.write(r.summary)


# =============================================================================
# Builder: construct a single markdown download (Report + Sources + optional logs)
# =============================================================================

def build_download_markdown(
    final_report_md: str,
    results: Optional[List[SearchResult]],
    include_logs: Optional[List[str]] = None,
    include_thoughts: Optional[List[str]] = None,
) -> str:
    """Assemble a single markdown document for download.

    Sections:
      1) Final Report
      2) Sources
      3) Thinking Log
      4) Live Log
    """
    parts: List[str] = []

    # 1) Final Report
    parts.append("# Final Report\n")
    parts.append(final_report_md.strip())
    parts.append("")

    # 2) Sources
    if results:
        parts.append("# Sources\n")
        for i, r in enumerate(results, start=1):
            parts.append(f"## [{i}] {r.title}\n")
            parts.append(f"- **URL:** {r.url}\n")
            parts.append(r.summary if r.summary else "")
            parts.append("")

    # 3) Thinking
    if include_thoughts:
        parts.append("# Thinking Log (captured snippets)\n")
        for t in include_thoughts:
            parts.append(f"- *{t}*")
        parts.append("")

    # 4) Live logs
    if include_logs:
        parts.append("# Live Log\n")
        parts.append("```text")
        parts.extend(include_logs)
        parts.append("```")
        parts.append("")

    return "\n".join(parts).strip()


# =============================================================================
# CHAT-FIRST mode UI
# =============================================================================
mode = "Chat-first (new)"  # retained for potential future branching

# Session state init
if "chat2" not in st.session_state:
    st.session_state.chat2 = []
if "is_running" not in st.session_state:
    st.session_state.is_running = False

# Render chat bubbles (history)
for role, text in st.session_state.chat2:
    with st.chat_message(role):
        st.markdown(text)

# Chat input; validator will ask follow-ups
msg = st.chat_input(
    "Tell me your topic, press enter, and I’ll ask follow-ups. Click '🚀 Start Research' when ready!"
)
if msg:
    # Stop any in-flight run
    st.session_state.is_running = False
    st.session_state.chat2.append(("user", msg))

    # Keep the last dozen lines for the validator
    transcript = "\n".join(f"{r.upper()}: {t}" for r, t in st.session_state.chat2[-12:])
    run = run_async(Runner.run(intention_validator_agent, input=transcript))
    out_text = run.final_output if isinstance(run.final_output, str) else str(run.final_output)
    st.session_state.chat2.append(("assistant", out_text))
    st.rerun()

# Action row: start streaming with whatever info we have
start = st.button("**🚀 Start Research**", use_container_width=True)
if start:
    st.session_state.is_running = True
    st.rerun()

# Active run; stream logs, thinking, report, and sources
if st.session_state.is_running:
    st.markdown("---")
    st.subheader("1) Thinking log")
    thinking_ph = st.empty()

    st.markdown("---")
    st.subheader("2) Live log")
    log_ph = st.empty()

    st.markdown("---")
    st.subheader("3) Final Report")
    report_ph = st.empty()

    st.markdown("---")

    # Wire redirector to Streamlit placeholders
    redirector = StreamlitRedirector(log_ph, thinking_ph)

    def on_log(msg: str) -> None:
        redirector.write(msg + "\n")

    def on_thought(msg: str) -> None:
        redirector.write("Thoughts: " + msg + "\n")

    # Build a user-only transcript for refinement
    user_msgs = [t for r, t in st.session_state.chat2[-40:] if r == "user"]
    transcript = "\n".join(user_msgs[-20:])  # exclude assistant lines

    # Try to coerce the validator into returning a single research question
    try:
        force = (
            "\n\n[[INSTRUCTION]] Consider ONLY the USER messages above. Ignore all ASSISTANT messages. "
            "Return ONE explicit research question in the user's voice. "
            "Output ONLY that question; no greetings, no extra text. [[/INSTRUCTION]]"
        )
        refine = run_async(Runner.run(intention_validator_agent, input=transcript + force))
        refined_query = (refine.final_output or "").strip()

        bad = refined_query.lower()
        if ("start research" in bad) or bad.startswith(("great", "understood", "okay", "sure")):
            refined_query = ""
    except Exception:
        refined_query = ""

    # Fallbacks: last user message; otherwise empty
    if not refined_query:
        last_user_msgs = [t for r, t in reversed(st.session_state.chat2) if r == "user"]
        refined_query = (last_user_msgs[0] if last_user_msgs else "").strip()

    # Anchor to today's date to avoid stale-year queries
    user_tz = st.session_state.get("user_timezone", _DEFAULT_TZ)
    today = datetime.now(ZoneInfo(user_tz)).strftime("%Y-%m-%d")
    refined_query = f"{refined_query} (as of {today})"

    # Small UI cue
    st.caption(f"Final research question: {refined_query}")

    # Run coordinator; stream via callbacks
    try:
        with st.spinner("Running deep research…"):
            coordinator = coord_mod.ResearchCoordinator(
                refined_query,
                on_log=on_log,
                on_thought=on_thought,
            )
            final_report_md: str = run_async(coordinator.research())

        # Show final report
        report_ph.markdown(final_report_md)

        # Show sources
        results = getattr(coordinator, "search_results", [])
        render_sources(results)

        # Build a combined markdown file for download (Report + Sources)
        download_md = build_download_markdown(
            final_report_md=final_report_md,
            results=results,
            # If later you want to include thoughts/logs, pass them in here:
            # include_thoughts=redirector._thoughts,
            # include_logs=redirector._buffer,
        )

        # Persist in session so it survives reruns
        st.session_state["download_md"] = download_md

        st.download_button(
            label="⬇️ Download Report (.md) — app will reset",
            data=download_md.encode("utf-8"),
            file_name="deep_research_report.md",
            mime="text/markdown",
            use_container_width=True,
            key=f"download_report_md_{hash(download_md)}",  # fresh key avoids stale events
            on_click=_arm_reset_after_download,              # arm reset for next rerun
        )

    except Exception as e:
        st.error(f"Something went wrong: {e}")
        st.exception(e)
    finally:
        # Stop running; prevents unintended auto-reruns
        st.session_state.is_running = False
