"""Detached SharkByte background agent process. Runs independently of the UI."""
import argparse
import json
import os
import time
from pathlib import Path

import requests
from sharkbyte_browser import search_and_read as browser_search_and_read

OLLAMA_URL = "http://localhost:11434"
MODEL = "qwen3:0.6b"
INTERVAL = 45
AGENT_TEMPERATURE = 0.7


def atomic_write(path, obj):
    path = Path(path); tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)

def read_state(path):
    try: return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception: return {}

def search(goal, folder, state):
    def progress(message):
        state['status'] = message
        activity = state.setdefault('activity', [])
        activity.append(time.strftime('%H:%M:%S') + '  ' + message)
        state['activity'] = activity[-100:]
        atomic_write(folder / 'state.json', state)
    progress('Starting headless Chromium research browser…')
    sources = browser_search_and_read(goal, progress, None, max_results=8, total_timeout=135)
    progress(f'Browser finished reading {len(sources)} source pages.')
    return sources


def ollama(messages):
    body = {'model': MODEL, 'messages': messages, 'stream': False,
            'think': False, 'options': {'temperature': float(AGENT_TEMPERATURE), 'num_predict': 1200, 'num_ctx': 8192}}
    r = requests.post(f'{OLLAMA_URL}/api/chat', json=body, timeout=(10, 180)); r.raise_for_status()
    data = r.json(); msg = data.get('message', {})
    answer = msg.get('content', '')
    return answer or 'The model returned an empty response. It will retry during the next research cycle.'

def process_inbox(folder, state):
    inbox = folder / 'inbox'; inbox.mkdir(exist_ok=True)
    for path in sorted(inbox.glob('*.json')):
        try:
            item = json.loads(path.read_text(encoding='utf-8'))
            user_text = item.get('text', '').strip()
            if not user_text: path.unlink(missing_ok=True); continue
            state.setdefault('messages', []).append({'role':'user','content':user_text})
            state['status'] = 'Responding to check-in'; atomic_write(folder/'state.json', state)
            answer = ollama([{'role':'system','content':f"You are {state['name']}, a persistent background research agent. Your goal is: {state['goal']}. Be honest about progress, use Markdown, cite source URLs when relevant."}] + state['messages'][-12:])
            state['messages'].append({'role':'assistant','content':answer})
            state['updates'].append(answer); state['updates'] = state['updates'][-30:]
            state.setdefault('activity', []).append(time.strftime('%H:%M:%S') + '  Research summary completed and saved.')
            state['activity'] = state['activity'][-100:]
            state['status'] = 'Working'; state['last_run'] = time.time()
            atomic_write(folder/'state.json', state)
        except Exception as exc:
            state['status'] = f'Chat error: {str(exc)[:120]}'
            atomic_write(folder/'state.json', state)
        finally:
            try: path.unlink(missing_ok=True)
            except Exception: pass

def main():
    global AGENT_TEMPERATURE
    parser = argparse.ArgumentParser(); parser.add_argument('--agent-dir', required=True)
    args = parser.parse_args(); folder = Path(args.agent_dir)
    config = read_state(folder/'config.json')
    if not config: return
    try:
        AGENT_TEMPERATURE = max(0.0, min(2.0, float(config.get('temperature', 0.7))))
    except (TypeError, ValueError):
        AGENT_TEMPERATURE = 0.7
    state = read_state(folder/'state.json') or {
        'id': config['id'], 'name': config['name'], 'goal': config['goal'],
        'status': 'Starting', 'running': True, 'updates': [], 'messages': [],
        'sources': [], 'activity': [], 'last_run': None, 'pid': os.getpid()
    }
    state.update({'running': True, 'pid': os.getpid(), 'status': 'Starting'})
    atomic_write(folder/'state.json', state)
    while not (folder/'stop').exists():
        try:
            process_inbox(folder, state)
            state['status'] = 'Researching'; atomic_write(folder/'state.json', state)
            sources = search(config['goal'], folder, state)
            if (folder/'stop').exists(): break
            state['status'] = 'Thinking'; state['sources'] = sources
            state.setdefault('activity', []).append(time.strftime('%H:%M:%S') + '  Sending collected pages to the local AI for analysis.')
            atomic_write(folder/'state.json', state)
            source_text = '\n\n'.join(f"SOURCE {i+1}: {s['title']}\nURL: {s['url']}\nSnippet: {s['snippet']}\nPage excerpt: {s['excerpt']}" for i,s in enumerate(sources))
            prompt = (f"Goal: {config['goal']}\n\nPrevious updates: {state.get('updates', [])[-3:]}\n\n"
                      f"New Google research pages actually read:\n{source_text}\n\nMake meaningful progress toward the goal. Verify claims, explain what you learned, identify gaps and next steps. Use Markdown. Include a Sources list with the title and exact URLs above; do not cite sources you did not read. Do not claim actions you did not perform.")
            answer = ollama([{'role':'system','content':'You are a persistent autonomous research agent. Work carefully and cite sources.'},
                             {'role':'user','content':prompt}])
            if (folder/'stop').exists(): break
            state['updates'].append(answer); state['updates'] = state['updates'][-30:]
            state.setdefault('activity', []).append(time.strftime('%H:%M:%S') + '  Research summary completed and saved.')
            state['activity'] = state['activity'][-100:]
            state['messages'].append({'role':'assistant','content':answer})
            state['status'] = 'Waiting for next research cycle'; state['last_run'] = time.time()
            atomic_write(folder/'state.json', state)
        except Exception as exc:
            state['status'] = f'Error: {str(exc)[:160]}'
            state.setdefault('activity', []).append(time.strftime('%H:%M:%S') + '  RECOVERABLE ERROR: ' + str(exc)[:180])
            state['activity'] = state['activity'][-100:]
            atomic_write(folder/'state.json', state)
        # Remain responsive to check-ins and stop requests while waiting.
        for _ in range(INTERVAL):
            if (folder/'stop').exists(): break
            process_inbox(folder, state)
            time.sleep(1)
    state['running'] = False; state['status'] = 'Stopped'; atomic_write(folder/'state.json', state)
    try: (folder/'stop').unlink(missing_ok=True)
    except Exception: pass

if __name__ == '__main__': main()
