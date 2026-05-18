"""
api/llm_config.py — LLM provider configuration for 欢欢 desktop pet.

Reads/writes user/config.json and makes direct API calls to the configured provider.
No external dependencies — stdlib only (urllib, json, ssl).
"""

import json
import ssl
import urllib.request
import urllib.error
from pathlib import Path

# ── Config file location ──────────────────────────────────────────────────────
# 优先使用 HERMES_WEBUI_USER_DIR（app bundle 模式），否则回退到仓库内路径（开发模式）
import os as _os
_user_dir_override = _os.environ.get('HERMES_WEBUI_USER_DIR', '')
if _user_dir_override:
    _USER_CONFIG_PATH = Path(_user_dir_override) / 'config.json'
else:
    _USER_CONFIG_PATH = Path(__file__).parent.parent / 'user' / 'config.json'

# ── Provider definitions ──────────────────────────────────────────────────────
PROVIDERS = {
    'minimax': {
        'name': 'MiniMax',
        'default_base_url': 'https://api.minimax.io/v1',
        'default_model': 'MiniMax-Text-01',
        'api_format': 'openai',
        'requires_key': True,
        'key_placeholder': 'eyJ...',
    },
    'claude': {
        'name': 'Claude',
        'default_base_url': 'https://api.anthropic.com',
        'default_model': 'claude-haiku-3-5',
        'api_format': 'anthropic',
        'requires_key': True,
        'key_placeholder': 'sk-ant-...',
    },
    'deepseek': {
        'name': 'DeepSeek',
        'default_base_url': 'https://api.deepseek.com/v1',
        'default_model': 'deepseek-chat',
        'api_format': 'openai',
        'requires_key': True,
        'key_placeholder': 'sk-...',
    },
    'openai': {
        'name': 'OpenAI',
        'default_base_url': 'https://api.openai.com/v1',
        'default_model': 'gpt-4o-mini',
        'api_format': 'openai',
        'requires_key': True,
        'key_placeholder': 'sk-...',
    },
    'qwen': {
        'name': 'Qwen 通义千问',
        'default_base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        'default_model': 'qwen-plus',
        'api_format': 'openai',
        'requires_key': True,
        'key_placeholder': 'sk-...',
    },
    'gemini': {
        'name': 'Gemini',
        'default_base_url': 'https://generativelanguage.googleapis.com/v1beta',
        'default_model': 'gemini-1.5-flash',
        'api_format': 'gemini',
        'requires_key': True,
        'key_placeholder': 'AIza...',
    },
    'ollama': {
        'name': 'Ollama 本地',
        'default_base_url': 'http://localhost:11434/v1',
        'default_model': 'llama3',
        'api_format': 'openai',
        'requires_key': False,
        'key_placeholder': '（本地无需 Key）',
    },
    'custom': {
        'name': '自定义',
        'default_base_url': '',
        'default_model': '',
        'api_format': 'openai',
        'requires_key': False,
        'key_placeholder': '（可选）',
    },
}


# ── Config read / write ───────────────────────────────────────────────────────

def load_llm_config() -> dict:
    """Load user/config.json. Returns {} if not found."""
    try:
        if _USER_CONFIG_PATH.exists():
            with open(_USER_CONFIG_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('llm', {})
    except Exception as e:
        print(f'[llm_config] load failed: {e}', flush=True)
    return {}


def save_llm_config(llm_cfg: dict) -> None:
    """Save llm section to user/config.json, then sync to Hermes config."""
    _USER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if _USER_CONFIG_PATH.exists():
        try:
            with open(_USER_CONFIG_PATH, 'r', encoding='utf-8') as f:
                existing = json.load(f)
        except Exception:
            pass
    existing['llm'] = llm_cfg
    with open(_USER_CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    print(f'[llm_config] saved provider={llm_cfg.get("provider")}', flush=True)
    # Sync to Hermes so the agent picks up the new key/provider immediately
    try:
        sync_to_hermes(llm_cfg)
    except Exception as _e:
        print(f'[llm_config] hermes sync warning: {_e}', flush=True)


# ── UI provider → Hermes mapping ──────────────────────────────────────────────
# Each entry: (hermes_provider, env_var_for_key, env_var_for_base_url)
_PROVIDER_MAP = {
    'minimax':  ('minimax-cn',  'MINIMAX_CN_API_KEY',  'MINIMAX_CN_BASE_URL'),
    'claude':   ('anthropic',   'ANTHROPIC_API_KEY',   None),
    'openai':   ('openai',      'OPENAI_API_KEY',      None),
    'deepseek': ('deepseek',    'DEEPSEEK_API_KEY',    None),
    'qwen':     ('qwen',        'DASHSCOPE_API_KEY',   None),
    'gemini':   ('google',      'GOOGLE_API_KEY',      None),
    'ollama':   ('openai',      None,                  None),   # local, no key
    'custom':   ('openai',      None,                  None),   # generic openai-compat
}


def _update_env_file(path: Path, updates: dict) -> None:
    """Update or append key=value pairs in a .env file (no quotes)."""
    lines = []
    if path.exists():
        try:
            lines = path.read_text(encoding='utf-8').splitlines()
        except Exception:
            pass
    updated_keys = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('#') or '=' not in stripped:
            new_lines.append(line)
            continue
        key = stripped.split('=', 1)[0].strip()
        if key in updates:
            new_lines.append(f'{key}={updates[key]}')
            updated_keys.add(key)
        else:
            new_lines.append(line)
    for key, val in updates.items():
        if key not in updated_keys:
            new_lines.append(f'{key}={val}')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(new_lines) + '\n', encoding='utf-8')


def _update_hermes_config_yaml(config_path: Path, hermes_provider: str,
                               model: str, base_url: str) -> None:
    """Update the model section in a Hermes config.yaml in-place using regex."""
    import re
    if not config_path.exists():
        return
    text = config_path.read_text(encoding='utf-8')

    def replace_or_append_in_model_block(text, key, value):
        # Try to replace existing key inside the 'model:' block
        pattern = rf'(model:\s*\n(?:[ \t]+\S[^\n]*\n)*?[ \t]+{re.escape(key)}:[ \t]*)[^\n]*'
        replacement = rf'\g<1>{value}'
        new_text, n = re.subn(pattern, replacement, text)
        if n:
            return new_text
        # Key not found in model block — inject it after 'model:' line
        return re.sub(r'(model:\s*\n)', rf'\1  {key}: {value}\n', text)

    if hermes_provider:
        text = replace_or_append_in_model_block(text, 'provider', hermes_provider)
    if model:
        text = replace_or_append_in_model_block(text, 'default', model)
    if base_url:
        text = replace_or_append_in_model_block(text, 'base_url', base_url)

    config_path.write_text(text, encoding='utf-8')


def sync_to_hermes(llm_cfg: dict) -> None:
    """Sync llm config to Hermes .env and config.yaml so the agent uses it.

    This updates:
      1. ~/.hermes/.env  (global)
      2. ~/.hermes/profiles/{active_profile}/.env
      3. ~/.hermes/profiles/{active_profile}/config.yaml  (model section)
    """
    provider = llm_cfg.get('provider', '')
    api_key  = llm_cfg.get('api_key', '').strip()
    base_url = llm_cfg.get('base_url', '').strip()
    model    = llm_cfg.get('model_id', '').strip()

    hermes_provider, env_key, env_base_url_key = _PROVIDER_MAP.get(
        provider, (None, None, None)
    )
    if not hermes_provider:
        print(f'[llm_config] unknown provider "{provider}", skipping hermes sync', flush=True)
        return

    home = Path(_os.environ.get('HOME', str(Path.home())))
    hermes_home = home / '.hermes'

    # Build env updates
    env_updates = {}
    if env_key and api_key:
        env_updates[env_key] = api_key
    if env_base_url_key and base_url:
        env_updates[env_base_url_key] = base_url

    # 1. Update global ~/.hermes/.env
    if env_updates:
        _update_env_file(hermes_home / '.env', env_updates)
        print(f'[llm_config] updated ~/.hermes/.env: {list(env_updates.keys())}', flush=True)

    # 2. Update all profile .env files
    profiles_dir = hermes_home / 'profiles'
    if profiles_dir.is_dir():
        for profile_dir in profiles_dir.iterdir():
            if profile_dir.is_dir():
                env_file = profile_dir / '.env'
                if env_updates:
                    _update_env_file(env_file, env_updates)

        # 3. Update active profile's config.yaml model section
        try:
            from api.profiles import get_active_hermes_home
            active_home = get_active_hermes_home()
        except Exception:
            active_home = hermes_home

        config_yaml = active_home / 'config.yaml'
        _update_hermes_config_yaml(
            config_yaml,
            hermes_provider=hermes_provider,
            model=model,
            base_url=base_url,
        )
        # Also update all profile config.yamls
        if profiles_dir.is_dir():
            for profile_dir in profiles_dir.iterdir():
                if profile_dir.is_dir():
                    _update_hermes_config_yaml(
                        profile_dir / 'config.yaml',
                        hermes_provider=hermes_provider,
                        model=model,
                        base_url=base_url,
                    )
        print(f'[llm_config] synced hermes: provider={hermes_provider} model={model}', flush=True)


def is_configured() -> bool:
    """Return True if a valid LLM config exists."""
    cfg = load_llm_config()
    provider = cfg.get('provider', '')
    if not provider or provider not in PROVIDERS:
        return False
    pinfo = PROVIDERS[provider]
    if pinfo['requires_key'] and not cfg.get('api_key', '').strip():
        return False
    return True


# ── Internal HTTP helper ──────────────────────────────────────────────────────

def _http_post(url: str, headers: dict, body: dict, timeout: int = 30) -> dict:
    """POST JSON to url, return parsed response dict. Raises on error."""
    data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers=headers, method='POST')
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _http_post_nossl(url: str, headers: dict, body: dict, timeout: int = 30) -> dict:
    """POST JSON without SSL verification (for local Ollama etc.)."""
    data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers=headers, method='POST')
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


# ── API call implementations ──────────────────────────────────────────────────

def _call_openai_compat(base_url: str, api_key: str, model: str,
                         messages: list, timeout: int = 60) -> str:
    """Call any OpenAI-compatible endpoint (MiniMax, DeepSeek, Qwen, Ollama, custom)."""
    url = base_url.rstrip('/') + '/chat/completions'
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {api_key}',
    }
    body = {
        'model': model,
        'messages': messages,
        'max_tokens': 512,
        'temperature': 0.7,
        'stream': False,
    }
    is_local = 'localhost' in url or '127.0.0.1' in url
    fn = _http_post_nossl if is_local else _http_post
    resp = fn(url, headers, body, timeout=timeout)
    return resp['choices'][0]['message']['content']


def _call_anthropic(base_url: str, api_key: str, model: str,
                     messages: list, system: str, timeout: int = 60) -> str:
    """Call Anthropic Claude API."""
    url = base_url.rstrip('/') + '/v1/messages'
    headers = {
        'Content-Type': 'application/json',
        'x-api-key': api_key,
        'anthropic-version': '2023-06-01',
    }
    # Separate system message from user/assistant turns
    user_messages = [m for m in messages if m['role'] != 'system']
    body = {
        'model': model,
        'max_tokens': 512,
        'messages': user_messages,
    }
    if system:
        body['system'] = system
    resp = _http_post(url, headers, body, timeout=timeout)
    return resp['content'][0]['text']


def _call_gemini(base_url: str, api_key: str, model: str,
                  messages: list, timeout: int = 60) -> str:
    """Call Google Gemini API."""
    url = f'{base_url.rstrip("/")}/models/{model}:generateContent?key={api_key}'
    headers = {'Content-Type': 'application/json'}
    # Convert to Gemini format
    parts = []
    for m in messages:
        if m['role'] in ('user', 'model'):
            parts.append({
                'role': 'user' if m['role'] == 'user' else 'model',
                'parts': [{'text': m['content']}],
            })
    body = {'contents': parts}
    resp = _http_post(url, headers, body, timeout=timeout)
    return resp['candidates'][0]['content']['parts'][0]['text']


# ── Format conversation history ───────────────────────────────────────────────

def _build_messages(history: list, new_message: str, system_prompt: str,
                     api_format: str) -> tuple:
    """
    Convert stored session messages + new user message into API-ready format.
    Returns (messages_list, system_str).
    """
    messages = []
    system_str = system_prompt or ''

    if api_format == 'anthropic':
        # Anthropic: system is separate, messages are user/assistant
        for m in history[-20:]:  # Last 20 messages for context
            role = m.get('role', '')
            content = m.get('content', '') or ''
            if isinstance(content, list):
                # Extract text from content blocks
                content = ' '.join(c.get('text', '') for c in content if isinstance(c, dict))
            if role in ('user', 'assistant') and content.strip():
                messages.append({'role': role, 'content': content})
        messages.append({'role': 'user', 'content': new_message})
    elif api_format == 'gemini':
        for m in history[-20:]:
            role = m.get('role', '')
            content = m.get('content', '') or ''
            if isinstance(content, list):
                content = ' '.join(c.get('text', '') for c in content if isinstance(c, dict))
            if role == 'user' and content.strip():
                messages.append({'role': 'user', 'content': content})
            elif role == 'assistant' and content.strip():
                messages.append({'role': 'model', 'content': content})
        messages.append({'role': 'user', 'content': new_message})
    else:
        # OpenAI-compatible: system message first
        if system_str:
            messages.append({'role': 'system', 'content': system_str})
        for m in history[-20:]:
            role = m.get('role', '')
            content = m.get('content', '') or ''
            if isinstance(content, list):
                content = ' '.join(c.get('text', '') for c in content if isinstance(c, dict))
            if role in ('user', 'assistant') and content.strip():
                messages.append({'role': role, 'content': content})
        messages.append({'role': 'user', 'content': new_message})

    return messages, system_str


# ── Public API ────────────────────────────────────────────────────────────────

def chat_with_config(message: str, history: list, system_prompt: str,
                     cfg: dict = None, timeout: int = 60) -> str:
    """
    Send a message to the configured LLM and return the reply text.
    cfg: llm config dict (from load_llm_config()). Loaded automatically if None.
    """
    if cfg is None:
        cfg = load_llm_config()

    provider = cfg.get('provider', 'minimax')
    pinfo = PROVIDERS.get(provider, PROVIDERS['custom'])
    api_format = pinfo['api_format']

    base_url = cfg.get('base_url', '').strip() or pinfo['default_base_url']
    model = cfg.get('model_id', '').strip() or pinfo['default_model']
    api_key = cfg.get('api_key', '').strip()

    messages, system_str = _build_messages(history, message, system_prompt, api_format)

    if api_format == 'anthropic':
        return _call_anthropic(base_url, api_key, model, messages, system_str, timeout)
    elif api_format == 'gemini':
        return _call_gemini(base_url, api_key, model, messages, timeout)
    else:
        return _call_openai_compat(base_url, api_key, model, messages, timeout)


def test_connection(cfg: dict) -> dict:
    """
    Test the LLM connection by sending a simple 'hello' message.
    Returns {'ok': True/False, 'message': str, 'latency_ms': int}.
    """
    import time
    t0 = time.time()
    try:
        reply = chat_with_config(
            message='hello',
            history=[],
            system_prompt='Reply with one short sentence.',
            cfg=cfg,
            timeout=20,
        )
        latency_ms = int((time.time() - t0) * 1000)
        return {'ok': True, 'message': f'连接成功 ✓ ({latency_ms}ms)', 'reply': reply[:100]}
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')[:200]
        return {'ok': False, 'message': f'连接失败：HTTP {e.code} — {body}'}
    except urllib.error.URLError as e:
        return {'ok': False, 'message': f'连接失败：无法连接到服务器 — {e.reason}'}
    except KeyError as e:
        return {'ok': False, 'message': f'连接失败：响应格式异常（{e}），请检查 Base URL 和 Model ID'}
    except Exception as e:
        return {'ok': False, 'message': f'连接失败：{e}'}
