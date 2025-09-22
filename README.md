# 🔎 Gian's Deep Research Agent — Streamlit

A web-based, multi-agent research pipeline with live streaming, intention validation, and clean source citations.

## Attribution & Inspiration
This project is inspired by an excellent console-based deep-research tool by **KodySimpson**.  
Original repository: https://github.com/KodySimpson/agents-sdk/tree/master/projects/deep_research  

What I kept: the core research loop logic and agent roles (Query, Search/Summarize, Follow-up, Synthesis).  
What I changed: 
- A full Streamlit UI (original code was console based UI using Rich library)
- An Intention Validator agent, and additional chat step in the beginning (big difference in how specific the research topic is going to be)
- Each AI Agent Prompt updates
- Adding match score by simple lexical relevance score so that the agent would not use and summarize all article it finds blindly (It's not perfect but it should help save some tokens from summarizing completely unrelated articles), added awareness of current date, and various other tweaks to make it work in Streamlit UI.

**Why did I start through KodySimpson's code**: While I was trying to self-learn OpenAI's SDK, I found his video tutorial, and I loved this idea of having your own deep-research agent (and using DuckDuckGo Search as it's a free alternative than OpenAI's SDK's built in internet search); and not to mention deep-research agent always interests me and I always wanted to understand how it works. His approach seemed simple but well-structured. I wanted to improve upon it and make my own version of it after understanding it.

## 🧭 Features
- Chat first before research -> Generates specific queries and report, not generic.
- Runs web searches and summarizes sources
- Decides whether to follow up with more queries (coverage, conflict, recency)
- Synthesizes a final report with in-text citations like `[1]`, `[2]`
- Shows a live log, “thinking” highlights, and collapsible sources
- Can download the report by clicking the download button.

## ✨ New vs. Original Console Repo
- Streamlit webapp
- Added Intention Validator Agent (clarifies scope, never auto-runs research) to the pipeline  
- Updated AI Agents prompts
- Updated awareness of most recent date to provide more relevant research  
- Interactive source panel: numbered titles, URLs, and summaries in collapsible expanders  
- ANSI stripping, resilient JSON extraction, configurable DuckDuckGo parameters  
- Added simple lexical relevance score to help summarize related articles, skipping completely unrelated ones.
- Synthesis agent nudged to always cite sources inline `[n]`  

## 🏗️ Architecture
User → Intention Validator (chat) → ResearchCoordinator
ResearchCoordinator → Query Agent → DuckDuckGo Search → Search Agent (summaries)
→ Follow-up Decision Agent → [loop if needed] → Synthesis Agent → Final Report + Sources

**Agents:**
- Intention Validator Agent: Domain clarifier, ensures user's research topic is very specific to their need.
- Query Agent: Gives diverse, high-value queries  
- Search Agent: 2–3 paragraph summaries from chosen web articles  
- Follow-up Agent: Checks the obtained results and decide whether to follow up (if yes, create certain number of questions)  
- Synthesis Agent: Final markdown with citations    

## 📦 Project Layout
- app.py # Streamlit UI (Chat-first interface)
- coordinator.py # Orchestrates research loop; streams via callbacks
- models.py # Pydantic models
- research_agents/ # All agents are here
- deep_research_tool.py # function_tool wrapper for one-shot runs

## 🚀 Quick Start
1. Clone the repo
    ```bash
    git clone <this-repo>
    cd <repo>
    ```
2. Create and activate a virtual environment
    ```bash
    python -m venv .venv
    source .venv/bin/activate   # Windows: .venv\Scripts\activate
    ```
3. Install dependencies
    ```bash
    pip install -r requirements.txt
    ```
4. Create a `.env` file and add:
    ```
    OPENAI_API_KEY=sk-...
    ```
5. Run the app
    ```bash
    streamlit run app.py
    ```

## 🖥️ Usage
- **Step 1:** Chat with validator, answer clarifying questions
- **Step 2:** Click 🚀 Start research now
- **Step 3:** View streamed log, report, and sources

## ⚙️ Config
- **In coordinator.py** you may change various parameters from line 86
But I suggest to change the below for more in-depth research (note the higher the number the more tokens your AI Agent will consume and thus it will cost more per research):

max_rounds: default 3
picks_per_query: default 2
results_per_query: default 3

- **In follow_up_agent.py**
Change 'xxx' inside task section:
- If yes, generate xxx concise follow-up research queries...

- **In query_agent.py**
Change 'xxx' inside task section:
- MAXIMUM 'xxx' queries to fully cover user's full ...


Models: defaults are gpt-4-1.mini / gpt-4o-mini (adjust per agent if you'd like)
However, based on experience gpt 4.1-mini is still considerably cheap and better at handling larger context window. Which is why I gave it the task for synthesizing and deciding whether to follow up or not.