\# SharkByte AI



SharkByte is a local AI assistant powered by Ollama. It includes a terminal interface, a browser-based chat interface, multiple assistant modes, background agents, file tools, and a localhost API.



\## What's new in this update



\* \*\*Long code blocks become project downloads.\*\* Code blocks at least 3,500 characters or 70 lines are marked with an orange file card showing the generated filename and extension. The Download Folder control creates a named project folder and a ZIP containing the source file and a small README. Shorter blocks remain individual code downloads.

\* \*\*Edit File\*\* is available from the `/` menu and through `/edit-file`. SharkByte reads a selected text file, prepares a proposed edit with the coding model, shows a preview, and does not write anything until you explicitly type `WRITE`. A `.bak` backup is created before the approved replacement.

\* \*\*Localhost API keys\*\* can be generated, named, listed, renamed, and deleted. Keys are saved in SharkByte's local data directory. The API server binds only to `127.0.0.1` and starts with SharkByte.

\* \*\*Install-Programs.bat\*\* downloads everything needed to run `app.py`.



\## Requirements



\* Windows is the primary supported setup described here.

\* Python 3.10 or newer (max is 3.13 for now).

\* \[Ollama](https://ollama.com/download), installed and running.

\* The existing `sharkbyte\\\\\\\_browser.py` helper module must remain beside `app.py` because the application imports it for web research.



Install Python packages from the project directory:



```powershell

py -m pip install -r requirements.txt

```



\## Installation / update



1\. Run `Install-Program.bat`

2\. Check python version `python --version`

3\. Install or update Python dependencies:



```powershell

&#x20;  py -m pip install -r requirements.txt

&#x20;  ```



4\. Download the configured models if they are not already installed:



```powershell

&#x20;  ollama pull qwen3:0.6b

&#x20;  ollama pull qwen2.5-coder:1.5b

&#x20;  ollama pull gemma3:1b

&#x20;  ollama pull llama3.2:3b

&#x20;  ollama pull phi3:mini

&#x20;  ollama pull embeddinggemma-2:270m

&#x20;  ```



5\. Start SharkByte:



```powershell

&#x20;  py app.py

&#x20;  ```



\## Assistant modes and default models



|Mode|Model|What it is for|

|-|-|-|

|`/chat`|`qwen3:0.6b` (or the model selected with `/models`)|General chat; follows your saved model selection|

|`/work`|`phi3:mini`|Fast work-focused tasks|

|`/coder`|`qwen2.5-coder:1.5b`|Coding and debugging|

|`/deep-thinking`|`qwen3:0.6b`|Reasoning-focused answers|

|`/casual`|`gemma3:1b`|Casual conversation|

|Agent coordinator|`qwen3:0.6b`|Coordinates background-agent research|

|Embedding helper|`embeddinggemma-2:270m`|Used by the standalone embedding helper|



\## Ollama model support



SharkByte's model selector and download catalog include the following 96 curated tags. The catalog is a convenience list, not a restriction on Ollama: `/models` can select installed models, and `/download models MODEL:TAG` can pull another valid Ollama tag.



\### Qwen and coding models



\* `qwen3:0.6b`, `qwen3:1.7b`, `qwen3:4b`, `qwen3:8b`, `qwen3:14b`, `qwen3:30b-a3b`

\* `qwen2.5-coder:0.5b`, `qwen2.5-coder:1.5b`, `qwen2.5-coder:3b`, `qwen2.5-coder:7b`

\* `qwen2.5:0.5b`, `qwen2.5:1.5b`, `qwen2.5:3b`, `qwen2.5:7b`, `qwen2.5:14b`, `qwen2.5:32b`, `qwen2.5:72b`

\* `qwen2.5vl:3b`, `qwen2.5vl:7b`, `qwen2.5vl:32b`

\* `qwen2.5-math:1.5b`, `qwen2.5-math:7b`, `qwen2.5-math:72b`

\* `deepseek-r1:1.5b`, `deepseek-r1:7b`, `deepseek-r1:8b`, `deepseek-r1:14b`, `deepseek-r1:32b`

\* `deepseek-coder:1.3b`, `deepseek-coder:6.7b`, `deepseek-coder:33b`

\* `codegemma:2b`, `codegemma:7b`, `starcoder2:3b`, `starcoder2:7b`, `starcoder2:15b`, `stable-code:3b`, `granite-code:3b`, `granite-code:8b`



\### Gemma, Llama, Phi, and small models



\* `gemma3:1b`, `gemma3:4b`, `gemma3:12b`, `gemma3:27b`

\* `gemma2:2b`, `gemma2:9b`, `gemma2:27b`, `gemma3n:e2b`, `gemma3n:e4b`

\* `llama3.2:1b`, `llama3.2:3b`, `llama3.1:8b`, `llama3.1:70b`, `llama3:8b`, `llama3:70b`

\* `phi3:mini`, `phi4:14b`

\* `smollm2:135m`, `smollm2:360m`, `smollm2:1.7b`, `tinyllama:1.1b`



\### Other chat, vision, and embedding models



\* `mistral:7b`, `mistral-nemo:12b`, `mixtral:8x7b`

\* `granite3.3:2b`, `granite3.3:8b`, `command-r:latest`, `command-r-plus:latest`

\* `dolphin3:8b`, `wizardlm2:7b`, `wizardlm2:8x22b`, `orca-mini:3b`, `orca-mini:7b`, `orca-mini:13b`

\* `openchat:7b`, `solar:10.7b`, `yi:6b`, `yi:9b`, `yi:34b`, `falcon:7b`, `falcon:40b`, `nous-hermes2:10.7b`, `nemotron-mini:4b`, `mathstral:7b`

\* `llama3.2-vision:11b`, `llama3.2-vision:90b`, `llava:7b`, `llava:13b`, `llava:34b`, `llava-llama3:8b`, `bakllava:7b`, `moondream:1.8b`, `minicpm-v:8b`

\* `nomic-embed-text`, `mxbai-embed-large`, `embeddinggemma:300m`, `embeddinggemma-2:270m`



A model must be installed in Ollama before SharkByte can use it. Hardware needs vary widely; large models can require substantial memory.



\## Commands



Commands are typed into the SharkByte terminal. Replace placeholders such as `QUERY`, `ID`, `PATH`, and `MODEL:TAG` with your own values.



\### Modes and help



|Command|Description|

|-|-|

|`/chat \\\\\\\[MESSAGE]`|Switch to Chat mode; optionally send a message. Chat uses the saved model selection.|

|`/work \\\\\\\[MESSAGE]`|Switch to Work mode; optionally send a message. Uses Phi-3 mini.|

|`/coder \\\\\\\[MESSAGE]`|Switch to Coder mode; optionally send a message.|

|`/deep-thinking \\\\\\\[QUESTION]`|Switch to Deep Thinking mode; optionally send a question.|

|`/casual \\\\\\\[MESSAGE]`|Switch to Casual mode; optionally send a message.|

|`/embed \\\\\\\[TEXT]`|Alias for Casual mode in this command handler.|

|`/help`|Show the command and keyboard help panel.|

|`/agents`|Open the persistent background-agents view.|

|`/quit`|Exit SharkByte.|



\### Models and downloads



|Command|Description|

|-|-|

|`/models`|Show installed Ollama models and open the selector.|

|`/models MODEL:TAG`|Select an installed model for Chat mode.|

|`/download models`|Open the curated Ollama model catalog.|

|`/download models MODEL:TAG`|Pull the specified Ollama model.|

|`/download-models`|Alias for opening the model catalog.|

|`/download-models MODEL:TAG`|Pull the specified model.|

|`/download QUERY`|Search for supported software or a file. SharkByte asks before downloading.|

|`/download HTTPS\\\\\\\_URL`|Start a supported download from a direct HTTPS URL.|

|`/download download`|Begin the explicit confirmation step for the selected download. Type `DOWNLOAD` when prompted.|

|`/download run`|Begin the explicit installer confirmation step. Type `RUN` when prompted.|



\### Research and agents



|Command|Description|

|-|-|

|`/search QUERY`|Search the web and read relevant sources.|

|`/agent GOAL`|Create a persistent background research agent.|

|`/agent-message ID TEXT`|Send an instruction to an agent.|

|`/stop-agent \\\\\\\[ID]`|Stop the specified agent; without an ID, uses the selected agent.|

|`/delete-agent \\\\\\\[ID]`|Delete an agent and its stored data; without an ID, uses the selected agent.|



\### Chats and history



|Command|Description|

|-|-|

|`/save-chat`|Save the current transcript as Markdown.|

|`/new` or `/new chat`|Save the current chat and start a new one.|

|`/chats`|List saved chats.|

|`/chats NUMBER`|Load a saved chat by its displayed number.|

|`/chats delete`|Delete the current chat.|

|`/chats delete current`|Delete the current chat.|

|`/chats delete-current`|Alias for deleting the current chat.|

|`/chats delete all`|Start the delete-all confirmation flow.|

|`/chats clear all`|Start the delete-all confirmation flow.|

|`/delete-chat`|Delete the current chat.|

|`/delete-current-chat`|Alias for deleting the current chat.|

|`/delete-all-chats yes`|Confirm permanent deletion of all saved chats and exports.|



\### Files and editing



|Command|Description|

|-|-|

|`/upload \\\\\\\[FILE PATH]`|Attach a readable local file to the next message.|

|`/edit-file`|Ask for a file path, then describe the edit. SharkByte previews the proposed complete file and waits for `WRITE` before changing the original.|

|`/edit-file PATH`|Start the same permission-based editing flow for the given path.|



Edit File only accepts UTF-8 text files up to 1 MB. It creates a `.bak` backup before writing. If you do not type `WRITE`, the original file is left unchanged.



\### API key management



|Command|Description|

|-|-|

|`/api-keys`|Open API key management.|

|`/api-keys add`|Start creating a key; SharkByte asks for a name before generating and saving it.|

|`/api-keys list`|Show every saved key, including its full value, in the local terminal.|

|`/api-keys delete NAME`|Delete a key by name.|

|`/api-keys rename OLD\\\_NAME|NEW\\\_NAME`|



API keys are stored locally in `%LOCALAPPDATA%\\\\\\\\\\\\\\\\SharkByte\\\\\\\\\\\\\\\\api\\\\\\\_keys.json` on Windows (or the app's local data directory on other platforms). They are bearer credentials; do not share them or commit the key file to source control.



\### Settings and utility commands



|Command|Description|

|-|-|

|`/temperature`|Show the current temperature.|

|`/temperature NUMBER`|Set temperature from `0` to `2`.|

|`/system-prompt`|Show the current system prompt.|

|`/system-prompt TEXT`|Replace the system prompt.|

|`/reset-temperature`|Restore the default temperature.|

|`/reset-system-prompt`|Restore the default system prompt.|

|`/clear`|Clear the visible conversation and AI history while keeping saved copies.|

|`/open`|Open the browser interface in a new tab.|

|`/open terminal`|Focus the SharkByte terminal from the browser interface.|



\## Localhost API



The API server starts automatically when SharkByte starts. It binds to `127.0.0.1` on a dynamically selected local port; it is not exposed as a public network service. When a key is created, SharkByte displays the API base URL and the new key.



All API requests require this header:



```http

Authorization: Bearer YOUR\\\\\\\_SHARKBYTE\\\\\\\_API\\\\\\\_KEY

```



\### `GET /api/v1/models`



Returns the locally installed Ollama model names.



\### `POST /api/v1/chat`



Send a non-streaming chat request:



```json

{

&#x20; "model": "qwen3:0.6b",

&#x20; "messages": \\\\\\\[

&#x20;   {"role": "user", "content": "Hello from my app"}

&#x20; ]

}

```



Example Python request:



```python

import requests



API\\\\\\\_BASE = "http://127.0.0.1:YOUR\\\\\\\_PORT/api/v1"

API\\\\\\\_KEY = "YOUR\\\\\\\_SHARKBYTE\\\\\\\_API\\\\\\\_KEY"



response = requests.post(

&#x20;   f"{API\\\\\\\_BASE}/chat",

&#x20;   headers={"Authorization": f"Bearer {API\\\\\\\_KEY}"},

&#x20;   json={

&#x20;       "model": "qwen3:0.6b",

&#x20;       "messages": \\\\\\\[{"role": "user", "content": "Hello from my app"}],

&#x20;   },

&#x20;   timeout=300,

)

response.raise\\\\\\\_for\\\\\\\_status()

print(response.json()\\\\\\\["message"]\\\\\\\["content"])

```



The port can change when SharkByte restarts. Create or list keys from `/api-keys` to see the current base URL/key details; the key itself remains saved.



\## Long code downloads



In the terminal transcript, code blocks with at least 3,500 characters or 70 lines show an orange-outlined file card with a generated filename and extension. Choose \*\*DOWNLOAD FOLDER\*\* to create a named project folder and ZIP archive in the SharkByte `downloads` directory. The folder contains the generated source file and a README. Smaller blocks use \*\*DOWNLOAD CODE\*\* and save a single file.



\## Keyboard controls



\* `F2` or `/`: open/close the menu (slash-menu toggle must be ON for `/`).

\* `Up` / `Down`: move through menu items or scroll the transcript.

\* `Page Up` / `Page Down`: move four menu items or scroll the transcript.

\* `Enter`: select a menu item.

\* `Escape`: close the menu.

\* `Ctrl+C`: cancel generation; when idle, exits SharkByte.



\## Troubleshooting



\### Ollama is unavailable



Make sure Ollama is running and check the installed models:



```powershell

ollama list

```



\### A configured model is missing



Pull it with the exact model tag, for example:



```powershell

ollama pull llama3.2:3b

```



\### API returns `401 unauthorized`



Check that the request uses the full key in `Authorization: Bearer ...`, that the key has not been deleted, and that the request is sent to the current localhost port displayed by SharkByte.



\### Edit File cannot read a path



Use a full path to an existing UTF-8 text file. Files larger than 1 MB and binary files are not accepted.



\## Notes



\* The API is loopback-only and requires a saved bearer key. It is available while SharkByte is running.

\* API keys are stored locally in readable form so the key manager can display all key values on request. Protect your Windows account and do not share `api\\\\\\\_keys.json`.

\* Edit File never overwrites a file until the user explicitly types `WRITE` after reviewing the proposal.

\* Ollama models have different hardware requirements and may not all support every capability.

\* The six Shark Byte Beta LLM's are coming soon.





