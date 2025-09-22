from __future__ import annotations
from typing import List, Callable, Optional

# Core agent runtime + tracing
from agents import Runner, trace
# DuckDuckGo Search (ddgs)
from ddgs import DDGS

# Domain-specific agents and models
from research_agents.query_agent import QueryResponse, query_agent
from research_agents.search_agent import search_agent
from research_agents.follow_up_agent import (
    FollowUpDecisionResponse,
    follow_up_decision_agent,
)
from research_agents.synthesis_agent import synthesis_agent

from models import SearchResult

import re

# -----------------------------------------------------------------------------
# Tokenization utilities for very lightweight lexical matching
# -----------------------------------------------------------------------------
# Use a frozen set for faster membership checks; lowercase only
_STOP_WORDS = frozenset({
    "the","a","an","and","or","of","in","on","for","to",
    "with","by","about","is","are","was","were","be"
})
# Precompile once; avoids recompiling on every call to _tok
_TOKEN_RE = re.compile(r"[^a-z0-9]+")

def _tok(s: str) -> list[str]:
    """Lowercase; strip to [a-z0-9] via regex; split; drop stopwords.

    This is intentionally simple; the goal is to get quick lexical overlap
    signals for ranking search hits without pulling in heavyweight NLP libs.
    """
    s = (s or "").lower()
    s = _TOKEN_RE.sub(" ", s)
    return [t for t in s.split() if t and t not in _STOP_WORDS]


def _match_score(query: str, title: str, url: str, snippet: str = "") -> float:
    """Compute a simple 0-1 lexical relevance score.

    Heuristic weights: title > snippet > url; plus a tiny phrase bonus when
    the query tokens appear as a contiguous substring in the title. This is
    deterministic and cheap; perfect for pre-ranking before LLM summarization.
    """
    q = set(_tok(query))
    if not q:
        return 0.0

    t = set(_tok(title))
    s = set(_tok(snippet))
    u = set(_tok(url))

    ov_title = len(q & t) / max(1, len(q))
    ov_snip  = len(q & s) / max(1, len(q))
    ov_url   = len(q & u) / max(1, len(q))

    # Tiny phrase bonus; encourages exact-ish matches in the title
    phrase_bonus = 0.1 if " ".join(_tok(query)) in " ".join(_tok(title)) else 0.0

    return 0.6 * ov_title + 0.25 * ov_snip + 0.15 * ov_url + phrase_bonus


class ResearchCoordinator:
    """
    Streamlit-friendly research orchestrator.

    Responsibilities:
      1) Generate search queries via the Query Agent
      2) Fetch web results via DuckDuckGo
      3) Summarize each picked result via the Search Agent
      4) Optionally loop additional rounds based on Follow-up Agent decision
      5) Synthesize a final markdown report via the Synthesis Agent

    Design choices:
      - No Rich/CLI dependencies; emit progress via callbacks for the UI.
      - Store data (queries, results) in fields; the UI decides how to render.
      - Use a tiny lexical ranker to filter noisy search results cheaply.
    """

    def __init__(
        self,
        query: str,
        max_rounds: int = 3,
        picks_per_query: int = 2,
        ddg_region: str = "us-en",
        ddg_safesearch: str = "on",
        ddg_timelimit: str | None = None,   # e.g., "d","w","m","y"; None = no time filter
        results_per_query: int = 3,
        on_log: Optional[Callable[[str], None]] = None,
        on_thought: Optional[Callable[[str], None]] = None,
    ) -> None:
        # User input
        self.query = query

        # Search + loop controls
        self.max_rounds = max_rounds
        self.picks_per_query = picks_per_query
        self.ddg_region = ddg_region
        self.ddg_safesearch = ddg_safesearch
        self.ddg_timelimit = ddg_timelimit
        self.results_per_query = results_per_query

        # Optional callbacks for UI streaming
        self.on_log = on_log
        self.on_thought = on_thought

        # Internal state accumulated across rounds
        self.search_results: List[SearchResult] = []
        self.generated_queries: List[str] = []

        # Reuse a single DDGS client; avoids re-instantiating per query
        self._ddg = DDGS()

    # ---------- small helpers ----------

    def _log(self, msg: str) -> None:
        """Emit a log line to the UI, if a callback was provided."""
        if self.on_log:
            self.on_log(msg)

    def _thought(self, msg: str) -> None:
        """Surface the model's chain-of-thought-like commentary to the UI."""
        if self.on_thought:
            self.on_thought(msg)

    def _section(self, title: str) -> None:
        """Pretty-print a section header; useful for user-visible logs."""
        bar = "-" * 72
        self._log(f"\n{bar}\n{title}\n{bar}\n")

    # ---------- main workflow ----------
    async def research(self) -> str:
        """Run the full research loop and return the final markdown report."""
        with trace("Deep Research Workflow"):
            # 1) Generate initial queries
            self._section("GENERATE QUERIES")
            qr = await Runner.run(query_agent, input=self.query)
            query_resp: QueryResponse = qr.final_output
            self.generated_queries = list(query_resp.queries)

            if query_resp.thoughts:
                self._thought(query_resp.thoughts)

            self._log("Generated Search Queries:")
            for i, q in enumerate(self.generated_queries, 1):
                self._log(f"  {i}. {q}")

            # 2) First-pass research (Round 1)
            round_no = 1
            self._section(f"RESEARCH ROUND {round_no}")
            await self._research_queries(self.generated_queries)

            # 3) Optional follow-up rounds (up to max_rounds)
            while round_no < self.max_rounds:
                self._section("EVALUATE FOLLOW-UP")
                decision = await self._follow_up_decision()

                status = "More research needed" if decision.should_follow_up else "Research complete"
                self._log(f"Decision: {status}")
                self._log(f"Reasoning: {decision.reasoning}")

                if not decision.should_follow_up:
                    self._log("No more research needed. Synthesizing report...")
                    break

                # Next round
                round_no += 1
                self._section(f"RESEARCH ROUND {round_no}")
                await self._research_queries(decision.queries)

            # 4) Synthesize final report
            self._section("SYNTHESIS")
            final_report = await self._synthesize()
            self._log("✓ Research complete!\n")
            return final_report

    # ---------- internals ----------
    def _ddg_search(self, q: str) -> list[dict]:
        """Run a DDG text search; return a list of result dicts.

        Resilient to transient failures; returns an empty list on exception.
        """
        try:
            return list(
                self._ddg.text(
                    q,
                    region=self.ddg_region,
                    safesearch=self.ddg_safesearch,
                    timelimit=self.ddg_timelimit,
                    max_results=self.results_per_query,
                )
            )
        except Exception:
            return []

    async def _research_queries(self, queries: List[str]) -> None:
        """Search, rank, and summarize for each query, appending SearchResult."""
        for q in queries:
            self._log(f"Searching for: {q}")
            raw = self._ddg_search(q)

            # Lightweight scoring to prioritize likely-relevant hits
            ranked: list[tuple[float, str, str]] = []  # (score, title, url)
            for r in raw:
                title = r.get("title", "")
                url   = r.get("href", "")
                snip  = r.get("body", "") or r.get("snippet", "") or ""
                score = _match_score(q, title, url, snip)

                # Internal debug logs; helpful for tuning thresholds
                self._log(f"[DEBUG] MatchScore={score:.2f} | Title={title} | URL={url}")
                ranked.append((score, title, url))

            # Sort by relevance; keep best N above threshold
            ranked.sort(reverse=True, key=lambda x: x[0])
            MIN_SCORE = 0.26  # simple threshold; tune as desired
            picks = [x for x in ranked if x[0] >= MIN_SCORE][: self.picks_per_query]
            if not picks and ranked:
                picks = ranked[:1]  # fallback: pick the single best so progress continues

            self._log(f"[DEBUG] Picked {len(picks)} of {len(ranked)} results (threshold {MIN_SCORE})")

            # Summarize each picked result via Search Agent
            for score, title, url in picks:
                self._log(f"  Picked (match {score:.2f}): {title}")
                self._log(f"  URL: {url}")
                self._log("  Analyzing content...")

                inp = f"Title: {title}\nURL: {url}"
                summary_run = await Runner.run(search_agent, input=inp)

                self.search_results.append(
                    SearchResult(title=title, url=url, summary=summary_run.final_output)
                )

                preview = summary_run.final_output[:100] + ("..." if len(summary_run.final_output) > 100 else "")
                self._log(f"  Summary: {preview}\n")

    def _findings_text(self) -> str:
        """Assemble a plain-text digest of current findings for the Follow-up Agent."""
        lines = [f"Original Query: {self.query}", "", "Current Findings:"]
        for i, r in enumerate(self.search_results, 1):
            lines.append(
                f"\n{i}. Title: {r.title}\n   URL: {r.url}\n   Summary: {r.summary}\n"
            )
        return "\n".join(lines)

    async def _follow_up_decision(self) -> FollowUpDecisionResponse:
        """Ask the Follow-up Agent whether to continue researching; return decision."""
        findings = self._findings_text()
        run = await Runner.run(follow_up_decision_agent, input=findings)
        decision: FollowUpDecisionResponse = run.final_output

        # Surface thoughts and proposed queries to the UI logs for transparency
        if getattr(decision, "thoughts", None):
            self._thought(decision.thoughts)

        if getattr(decision, "queries", None):
            self._log("Proposed follow-up queries:")
            for i, q in enumerate(decision.queries, 1):
                self._log(f"  {i}. {q}")

        return decision

    async def _synthesize(self) -> str:
        """Call the Synthesis Agent with explicit guidance to use [n]-style citations."""
        # Nudge the model toward in-text square-bracket citations; avoid LaTeX
        findings_text = (
            "Instruction: In your report, use in-text citations with square brackets "
            "corresponding to the numbered sources below, e.g., [1], [2]. \n\n"
            "IMPORTANT: Do NOT use LaTeX math delimiters `$...` or `$$...$$`. "
            "If you need currency, write `USD 1,000` (not `$1,000`).\n\n"
            f"Query: {self.query}\n\nSearch Results:\n"
        )
        for i, r in enumerate(self.search_results, 1):
            findings_text += (
                f"\n{i}. Title: {r.title}\n   URL: {r.url}\n   Summary: {r.summary}\n"
            )

        run = await Runner.run(synthesis_agent, input=findings_text)
        return run.final_output
