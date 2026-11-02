SHARKBYTE - LOCAL TERMINAL AI
============================

STARTING
1. Extract the entire SharkByte folder.
2. Double-click "Run SharkByte.bat".
3. The launcher installs Python packages and the headless Chromium browser if needed.
   The first Chromium installation can take a few minutes. Later launches reuse it.
4. Make sure Ollama is installed and the configured models are available:
   - qwen3:0.6b
   - qwen2.5-coder:0.5b
   - embeddinggemma-2:270m
   Pull any missing model with `ollama pull MODEL_NAME`.

BACKGROUND WEB RESEARCH
SharkByte uses Playwright with headless Chromium. It tries DuckDuckGo, Bing, then Google,
extracts result links using engine-specific selectors, and reads pages individually. Each
engine and page has a timeout; one blocked site cannot freeze the whole search. Some sites
use CAPTCHAs or block automation and are skipped. No browser can guarantee every site works.

BACKGROUND AGENTS
Agents run as detached Python processes and continue after the SharkByte window closes,
while Windows is running and the computer is awake. Up to five agents can be active.
Open AGENTS, select an agent with Up/Down, and press Enter with the input line empty to
open or close its activity view. The view shows browser actions, errors, sources, and
research updates. Type a message and press Enter to send a check-in to the selected agent.

AI MODES
Use AUTO to route coding prompts to Qwen2.5-Coder and general questions to Qwen3.
Long or reasoning-heavy prompts in AUTO use Qwen3 thinking mode. CHAT, CODER, DEEP THINKING,
and EMBED remain selectable manually. AUTO checks the models installed in Ollama and falls
back to the available local model if a configured model is missing.

STUCK REQUEST PROTECTION
Chat generations have socket and total-time limits. A watchdog releases the interface if a
worker stalls; Ctrl-C cancels the current request. Late output from a timed-out request is
ignored. Web search has bounded engine/page timeouts and a maximum number of follow-up rounds.

TEMPERATURE
Use SET TEMPERATURE or /temperature NUMBER (0 to 2). The current value is shown in the
status line. Each new Chat, Coder, Deep Thinking, and web-research request snapshots the
current value when submitted, so changing it later does not alter an active request.
Reset Temperature restores 0.7. Newly deployed background agents inherit the current
value when they are created.

NOTES
- Keep app.py, sharkbyte_agent_worker.py, sharkbyte_browser.py, requirements.txt, and the
  launcher in the same folder.
- Do not move or delete the SharkByte data folder while agents are running.
- To stop an agent, select it and use STOP SELECTED AGENT or /stop-agent ID.
- Browser search is not error-proof: CAPTCHA pages, network outages, and site restrictions
  can still prevent individual pages from being read. SharkByte skips failed pages rather
  than pretending it read them.
