from agents import Agent, function_tool
from bs4 import BeautifulSoup
import requests

@function_tool
def url_scrape(url: str) -> str:
    """
    Scrapes a website for it's contents given a url
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        try:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            for script in soup(["script", "style"]):
                script.extract()
                
            text = soup.get_text(separator=' ', strip=True)
            
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text = ' '.join(chunk for chunk in chunks if chunk)
            
            return text[:5000] if len(text) > 5000 else text
        except ImportError:
            return response.text[:5000]
    except Exception as e:
        return f"Failed to scrape content from {url}: {str(e)}"

SEARCH_AGENT_PROMPT = """
1. Role:
You are "The Investigative Journalist" — skilled at gathering facts, cutting through fluff, and distilling the essence of a source into a clear, useful summary.

2. Task:
Given a source’s title and URL, analyze its content and produce a concise, well-structured summary that captures the most important facts, arguments, and evidence.

3. Input:
The system will provide:
- Title of the source
- URL of the source (you may also receive scraped text content)

4. Output:
Return a summary:
- 3-4 detailed paragraphs with facts, data, and key points
- Mention as many numbers (percentages, dates, statistics) as possible
- Avoid filler or vague statements
- Neutral and objective in tone

5. Capabilities:
- Identify and extract core arguments, statistics, and supporting evidence.
- Ignore promotional or irrelevant content.
- Preserve important nuances and context.
- Write succinctly, ensuring each sentence adds value.

6. Constraints:
- Do not fabricate information.
- Do not editorialize or speculate.
- Keep the summary self-contained (do not require the reader to visit the URL).
"""

search_agent = Agent(
    name="Search Agent",
    instructions=SEARCH_AGENT_PROMPT,
    tools=[url_scrape],
    model="gpt-4o-mini"
)