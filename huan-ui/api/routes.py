"""
Hermes Web UI -- Route handlers for GET and POST endpoints.
Extracted from server.py (Sprint 11) so server.py is a thin shell.
"""
import html as _html
import json
import os
import platform
import queue
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs

from api.config import (
    STATE_DIR, SESSION_DIR, DEFAULT_WORKSPACE, DEFAULT_MODEL,
    SESSIONS, SESSIONS_MAX, LOCK, STREAMS, STREAMS_LOCK, CANCEL_FLAGS,
    SERVER_START_TIME, CLI_TOOLSETS, _INDEX_HTML_PATH, get_available_models,
    IMAGE_EXTS, MD_EXTS, MIME_MAP, MAX_FILE_BYTES, MAX_UPLOAD_BYTES,
    CHAT_LOCK, load_settings, save_settings,
)
from api.helpers import require, bad, safe_resolve, j, t, read_body, _security_headers, _sanitize_error

# ── CSRF: validate Origin/Referer on POST ────────────────────────────────────
import re as _re
def _check_csrf(handler) -> bool:
    """Reject cross-origin POST requests. Returns True if OK."""
    origin = handler.headers.get('Origin', '')
    referer = handler.headers.get('Referer', '')
    host = handler.headers.get('Host', '')
    if not origin and not referer:
        return True  # non-browser clients (curl, agent) have no Origin
    target = origin or referer
    # Extract host:port from origin/referer
    m = _re.match(r'^https?://([^/]+)', target)
    if not m:
        return False
    origin_host = m.group(1)
    # Allow same-origin: check Host, X-Forwarded-Host (reverse proxy), and
    # X-Real-Host against the origin. Reverse proxies (Caddy, nginx) set
    # X-Forwarded-Host to the client's original Host header.
    allowed_hosts = {h.strip() for h in [
        host,
        handler.headers.get('X-Forwarded-Host', ''),
        handler.headers.get('X-Real-Host', ''),
    ] if h.strip()}
    if origin_host in allowed_hosts:
        return True

    # Allow localhost-to-localhost requests (different ports, same machine)
    # This is safe for development as both are local
    m_origin = _re.match(r'^([^:]+)', origin_host)
    m_host = _re.match(r'^([^:]+)', host)
    if m_origin and m_host:
        origin_hostname = m_origin.group(1)
        backend_hostname = m_host.group(1)
        if origin_hostname == backend_hostname and origin_hostname in ('localhost', '127.0.0.1', '::1'):
            return True

    return False
from api.models import (
    Session, get_session, new_session, all_sessions, title_from,
    _write_session_index, SESSION_INDEX_FILE,
    load_projects, save_projects, import_cli_session,
    get_cli_sessions, get_cli_session_messages,
)
from api.workspace import (
    load_workspaces, save_workspaces, get_last_workspace, set_last_workspace,
    list_dir, read_file_content, safe_resolve_ws,
)
from api.upload import handle_upload
from api.streaming import _sse, _run_agent_streaming, cancel_stream

# Approval system (optional -- graceful fallback if agent not available)
try:
    from tools.approval import (
        has_pending, pop_pending, submit_pending,
        approve_session, approve_permanent, save_permanent_allowlist,
        is_approved, _pending, _lock, _permanent_approved,
        resolve_gateway_approval,
    )
except ImportError:
    has_pending = lambda *a, **k: False
    pop_pending = lambda *a, **k: None
    submit_pending = lambda *a, **k: None
    approve_session = lambda *a, **k: None
    approve_permanent = lambda *a, **k: None
    save_permanent_allowlist = lambda *a, **k: None
    is_approved = lambda *a, **k: True
    resolve_gateway_approval = lambda *a, **k: 0
    _pending = {}
    _lock = threading.Lock()
    _permanent_approved = set()


# ── Login page locale strings ─────────────────────────────────────────────────
# Add entries here to support more languages on the login page.
# The key must match the 'language' setting value (from static/i18n.js LOCALES).
_LOGIN_LOCALE = {
    'en': {
        'lang': 'en', 'title': 'Sign in',
        'subtitle': 'Enter your password to continue',
        'placeholder': 'Password', 'btn': 'Sign in',
        'invalid_pw': 'Invalid password', 'conn_failed': 'Connection failed',
    },
    'zh': {
        'lang': 'zh-CN', 'title': '\u767b\u5f55',
        'subtitle': '\u8f93\u5165\u5bc6\u7801\u7ee7\u7eed\u4f7f\u7528',
        'placeholder': '\u5bc6\u7801', 'btn': '\u767b\u5f55',
        'invalid_pw': '\u5bc6\u7801\u9519\u8bef', 'conn_failed': '\u8fde\u63a5\u5931\u8d25',
    },
}

# ── Login page (self-contained, no external deps) ────────────────────────────
_LOGIN_PAGE_HTML = '''<!doctype html>
<html lang="{{LANG}}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{BOT_NAME}} — {{LOGIN_TITLE}}</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#1a1a2e;color:#e8e8f0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
  height:100vh;display:flex;align-items:center;justify-content:center}
.card{background:#16213e;border:1px solid rgba(255,255,255,.08);border-radius:16px;padding:36px 32px;
  width:320px;text-align:center;box-shadow:0 8px 32px rgba(0,0,0,.3)}
.logo{width:48px;height:48px;border-radius:12px;background:linear-gradient(145deg,#e8a030,#e94560);
  display:flex;align-items:center;justify-content:center;font-weight:800;font-size:20px;color:#fff;
  margin:0 auto 12px;box-shadow:0 2px 12px rgba(233,69,96,.3)}
h1{font-size:18px;font-weight:600;margin-bottom:4px}
.sub{font-size:12px;color:#8888aa;margin-bottom:24px}
input{width:100%;padding:10px 14px;border-radius:10px;border:1px solid rgba(255,255,255,.1);
  background:rgba(255,255,255,.04);color:#e8e8f0;font-size:14px;outline:none;margin-bottom:14px;
  transition:border-color .15s}
input:focus{border-color:rgba(124,185,255,.5);box-shadow:0 0 0 3px rgba(124,185,255,.1)}
button{width:100%;padding:10px;border-radius:10px;border:none;background:rgba(124,185,255,.15);
  border:1px solid rgba(124,185,255,.3);color:#7cb9ff;font-size:14px;font-weight:600;cursor:pointer;
  transition:all .15s}
button:hover{background:rgba(124,185,255,.25)}
.err{color:#e94560;font-size:12px;margin-top:10px;display:none}
</style></head><body>
<div class="card">
  <div class="logo">{{BOT_NAME_INITIAL}}</div>
  <h1>{{BOT_NAME}}</h1>
  <p class="sub">{{LOGIN_SUBTITLE}}</p>
  <form onsubmit="doLogin(event);return false">
    <input type="password" id="pw" placeholder="{{LOGIN_PLACEHOLDER}}" autofocus
           onkeydown="if(event.key==='Enter'){doLogin(event);event.preventDefault();}">
    <button type="submit">{{LOGIN_BTN}}</button>
  </form>
  <div class="err" id="err"></div>
</div>
<script>
async function doLogin(e){
  e.preventDefault();
  const pw=document.getElementById('pw').value;
  const err=document.getElementById('err');
  err.style.display='none';
  try{
    const res=await fetch('/api/auth/login',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({password:pw}),credentials:'include'});
    const data=await res.json();
    if(res.ok&&data.ok){window.location.href='/';}
    else{err.textContent=data.error||'{{LOGIN_INVALID_PW}}';err.style.display='block';}
  }catch(ex){err.textContent='{{LOGIN_CONN_FAILED}}';err.style.display='block';}
}
</script></body></html>'''

# ── GET routes ────────────────────────────────────────────────────────────────

def handle_get(handler, parsed) -> bool:
    """Handle all GET routes. Returns True if handled, False for 404."""

    if parsed.path in ('/', '/index.html'):
        return t(handler, _INDEX_HTML_PATH.read_text(encoding='utf-8'),
                 content_type='text/html; charset=utf-8')

    if parsed.path == '/login':
        _settings = load_settings()
        _bn = _html.escape(_settings.get('bot_name') or 'Hermes')
        _lang = _settings.get('language', 'en')
        _login_strings = _LOGIN_LOCALE.get(_lang, _LOGIN_LOCALE['en'])
        _page = (
            _LOGIN_PAGE_HTML
            .replace('{{BOT_NAME}}', _bn)
            .replace('{{BOT_NAME_INITIAL}}', _bn[0].upper())
            .replace('{{LANG}}', _html.escape(_login_strings['lang']))
            .replace('{{LOGIN_TITLE}}', _html.escape(_login_strings['title']))
            .replace('{{LOGIN_SUBTITLE}}', _html.escape(_login_strings['subtitle']))
            .replace('{{LOGIN_PLACEHOLDER}}', _html.escape(_login_strings['placeholder']))
            .replace('{{LOGIN_BTN}}', _html.escape(_login_strings['btn']))
            .replace('{{LOGIN_INVALID_PW}}', _login_strings['invalid_pw'].replace('\\','\\\\').replace("'","\\'"))
            .replace('{{LOGIN_CONN_FAILED}}', _login_strings['conn_failed'].replace('\\','\\\\').replace("'","\\'"))
        )
        return t(handler, _page, content_type='text/html; charset=utf-8')

    if parsed.path == '/api/auth/status':
        from api.auth import is_auth_enabled, parse_cookie, verify_session
        logged_in = False
        if is_auth_enabled():
            cv = parse_cookie(handler)
            logged_in = bool(cv and verify_session(cv))
        return j(handler, {'auth_enabled': is_auth_enabled(), 'logged_in': logged_in})

    if parsed.path == '/favicon.ico':
        handler.send_response(204); handler.end_headers(); return True

    if parsed.path == '/health':
        with STREAMS_LOCK: n_streams = len(STREAMS)
        return j(handler, {
            'status': 'ok', 'sessions': len(SESSIONS),
            'active_streams': n_streams,
            'uptime_seconds': round(time.time() - SERVER_START_TIME, 1),
        })

    if parsed.path == '/api/models':
        return j(handler, get_available_models())

    if parsed.path == '/api/settings':
        settings = load_settings()
        # Never expose the stored password hash to clients
        settings.pop('password_hash', None)
        return j(handler, settings)

    if parsed.path.startswith('/static/'):
        return _serve_static(handler, parsed)

    if parsed.path == '/api/session':
        sid = parse_qs(parsed.query).get('session_id', [''])[0]
        if not sid:
            return j(handler, {'error': 'session_id is required'}, status=400)
        try:
            s = get_session(sid)
            return j(handler, {'session': s.compact() | {
                'messages': s.messages,
                'tool_calls': getattr(s, 'tool_calls', []),
            }})
        except KeyError:
            # Not a WebUI session -- try CLI store
            msgs = get_cli_session_messages(sid)
            if msgs:
                cli_meta = None
                for cs in get_cli_sessions():
                    if cs['session_id'] == sid:
                        cli_meta = cs
                        break
                sess = {
                    'session_id': sid,
                    'title': (cli_meta or {}).get('title', 'CLI Session'),
                    'workspace': (cli_meta or {}).get('workspace', ''),
                    'model': (cli_meta or {}).get('model', 'unknown'),
                    'message_count': len(msgs),
                    'created_at': (cli_meta or {}).get('created_at', 0),
                    'updated_at': (cli_meta or {}).get('updated_at', 0),
                    'pinned': False,
                    'archived': False,
                    'project_id': None,
                    'profile': (cli_meta or {}).get('profile'),
                    'is_cli_session': True,
                    'messages': msgs,
                    'tool_calls': [],
                }
                return j(handler, {'session': sess})
            return bad(handler, 'Session not found', 404)

    if parsed.path == '/api/sessions':
        webui_sessions = all_sessions()
        settings = load_settings()
        if settings.get('show_cli_sessions'):
            cli = get_cli_sessions()
            webui_ids = {s['session_id'] for s in webui_sessions}
            deduped_cli = [s for s in cli if s['session_id'] not in webui_ids]
        else:
            deduped_cli = []
        merged = webui_sessions + deduped_cli
        merged.sort(key=lambda s: s.get('updated_at', 0) or 0, reverse=True)
        return j(handler, {'sessions': merged, 'cli_count': len(deduped_cli)})

    if parsed.path == '/api/projects':
        return j(handler, {'projects': load_projects()})

    if parsed.path == '/api/session/export':
        return _handle_session_export(handler, parsed)

    if parsed.path == '/api/workspaces':
        return j(handler, {'workspaces': load_workspaces(), 'last': get_last_workspace()})

    if parsed.path == '/api/sessions/search':
        return _handle_sessions_search(handler, parsed)

    if parsed.path == '/api/list':
        return _handle_list_dir(handler, parsed)

    if parsed.path == '/api/personalities':
        # Read personalities from config.yaml agent.personalities section
        # (matches hermes-agent CLI behavior, not filesystem SOUL.md approach)
        from api.config import reload_config as _reload_cfg
        _reload_cfg()  # pick up config.yaml changes without server restart
        from api.config import get_config as _get_cfg
        _cfg = _get_cfg()
        agent_cfg = _cfg.get('agent', {})
        raw_personalities = agent_cfg.get('personalities', {})
        personalities = []
        if isinstance(raw_personalities, dict):
            for name, value in raw_personalities.items():
                desc = ''
                if isinstance(value, dict):
                    desc = value.get('description', '')
                elif isinstance(value, str):
                    desc = value[:80] + ('...' if len(value) > 80 else '')
                personalities.append({'name': name, 'description': desc})
        return j(handler, {'personalities': personalities})

    if parsed.path == '/api/characters/list':
        return _handle_characters_list(handler)

    if parsed.path.startswith('/api/frames/'):
        return _handle_frames(handler, parsed)

    if parsed.path == '/api/git-info':
        qs = parse_qs(parsed.query)
        sid = qs.get('session_id', [''])[0]
        if not sid:
            return bad(handler, 'session_id required')
        try:
            s = get_session(sid)
        except KeyError:
            return bad(handler, 'Session not found', 404)
        from api.workspace import git_info_for_workspace
        info = git_info_for_workspace(Path(s.workspace))
        return j(handler, {'git': info})

    if parsed.path == '/api/updates/check':
        settings = load_settings()
        if not settings.get('check_for_updates', True):
            return j(handler, {'disabled': True})
        qs = parse_qs(parsed.query)
        force = qs.get('force', ['0'])[0] == '1'
        # ?simulate=1 returns fake behind counts for UI testing (localhost only)
        if qs.get('simulate', ['0'])[0] == '1' and handler.client_address[0] == '127.0.0.1':
            return j(handler, {
                'webui': {'name': 'webui', 'behind': 3, 'current_sha': 'abc1234', 'latest_sha': 'def5678', 'branch': 'master'},
                'agent': {'name': 'agent', 'behind': 1, 'current_sha': 'aaa0001', 'latest_sha': 'bbb0002', 'branch': 'master'},
                'checked_at': 0,
            })
        from api.updates import check_for_updates
        return j(handler, check_for_updates(force=force))

    if parsed.path == '/api/chat/stream/status':
        stream_id = parse_qs(parsed.query).get('stream_id', [''])[0]
        return j(handler, {'active': stream_id in STREAMS, 'stream_id': stream_id})

    if parsed.path == '/api/chat/cancel':
        stream_id = parse_qs(parsed.query).get('stream_id', [''])[0]
        if not stream_id:
            return bad(handler, 'stream_id required')
        cancelled = cancel_stream(stream_id)
        return j(handler, {'ok': True, 'cancelled': cancelled, 'stream_id': stream_id})

    if parsed.path == '/api/chat/stream':
        return _handle_sse_stream(handler, parsed)

    if parsed.path == '/api/file/raw':
        return _handle_file_raw(handler, parsed)

    if parsed.path == '/api/file':
        return _handle_file_read(handler, parsed)

    if parsed.path == '/api/approval/pending':
        return _handle_approval_pending(handler, parsed)

    if parsed.path == '/api/approval/inject_test':
        # Loopback-only: used by automated tests; blocked from any remote client
        if handler.client_address[0] != '127.0.0.1':
            return j(handler, {'error': 'not found'}, status=404)
        return _handle_approval_inject(handler, parsed)

    # ── Cron API (GET) ──
    if parsed.path == '/api/crons':
        from cron.jobs import list_jobs
        return j(handler, {'jobs': list_jobs(include_disabled=True)})

    if parsed.path == '/api/crons/output':
        return _handle_cron_output(handler, parsed)

    if parsed.path == '/api/crons/recent':
        return _handle_cron_recent(handler, parsed)

    # ── Skills API (GET) ──
    if parsed.path == '/api/skills':
        from tools.skills_tool import skills_list as _skills_list
        raw = _skills_list()
        data = json.loads(raw) if isinstance(raw, str) else raw
        return j(handler, {'skills': data.get('skills', [])})

    if parsed.path == '/api/skills/content':
        from tools.skills_tool import skill_view as _skill_view, SKILLS_DIR
        qs = parse_qs(parsed.query)
        name = qs.get('name', [''])[0]
        if not name: return j(handler, {'error': 'name required'}, status=400)
        file_path = qs.get('file', [''])[0]
        if file_path:
            # Serve a linked file from the skill directory
            import re as _re
            if _re.search(r'[*?\[\]]', name):
                return bad(handler, 'Invalid skill name', 400)
            skill_dir = None
            for p in SKILLS_DIR.rglob(name):
                if p.is_dir(): skill_dir = p; break
            if not skill_dir: return bad(handler, 'Skill not found', 404)
            target = (skill_dir / file_path).resolve()
            try: target.relative_to(skill_dir.resolve())
            except ValueError: return bad(handler, 'Invalid file path', 400)
            if not target.exists() or not target.is_file():
                return bad(handler, 'File not found', 404)
            return j(handler, {'content': target.read_text(encoding='utf-8'), 'path': file_path})
        raw = _skill_view(name)
        data = json.loads(raw) if isinstance(raw, str) else raw
        if 'linked_files' not in data: data['linked_files'] = {}
        return j(handler, data)

    # ── Memory API (GET) ──
    if parsed.path == '/api/memory':
        return _handle_memory_read(handler)

    # ── Profile API (GET) ──
    if parsed.path == '/api/profiles':
        from api.profiles import list_profiles_api, get_active_profile_name
        return j(handler, {'profiles': list_profiles_api(), 'active': get_active_profile_name()})

    if parsed.path == '/api/profile/active':
        from api.profiles import get_active_profile_name, get_active_hermes_home
        return j(handler, {'name': get_active_profile_name(), 'path': str(get_active_hermes_home())})

    # ── LLM config (AI 配置) ───────────────────────────────────────────────────
    if parsed.path == '/api/llm-config':
        from api.llm_config import load_llm_config, PROVIDERS
        cfg = load_llm_config()
        # Mask API key for display
        display_cfg = dict(cfg)
        key = display_cfg.get('api_key', '')
        if key:
            display_cfg['api_key_masked'] = key[:6] + '****' if len(key) > 6 else '****'
            display_cfg['api_key'] = ''  # never send full key to browser
        return j(handler, {'llm': display_cfg, 'providers': PROVIDERS})

    return False  # 404


# ── POST routes ───────────────────────────────────────────────────────────────

def handle_post(handler, parsed) -> bool:
    """Handle all POST routes. Returns True if handled, False for 404."""
    # CSRF: reject cross-origin browser requests
    if not _check_csrf(handler):
        return j(handler, {'error': 'Cross-origin request rejected'}, status=403)

    if parsed.path == '/api/upload':
        return handle_upload(handler)

    body = read_body(handler)

    if parsed.path == '/api/session/new':
        s = new_session(workspace=body.get('workspace'), model=body.get('model'))
        return j(handler, {'session': s.compact() | {'messages': s.messages}})

    if parsed.path == '/api/sessions/cleanup':
        return _handle_sessions_cleanup(handler, body, zero_only=False)

    if parsed.path == '/api/sessions/cleanup_zero_message':
        return _handle_sessions_cleanup(handler, body, zero_only=True)

    if parsed.path == '/api/session/rename':
        try: require(body, 'session_id', 'title')
        except ValueError as e: return bad(handler, str(e))
        try: s = get_session(body['session_id'])
        except KeyError: return bad(handler, 'Session not found', 404)
        s.title = str(body['title']).strip()[:80] or 'Untitled'
        s.save()
        return j(handler, {'session': s.compact()})

    if parsed.path == '/api/personality/set':
        try: require(body, 'session_id')
        except ValueError as e: return bad(handler, str(e))
        if 'name' not in body:
            return bad(handler, 'Missing required field: name')
        sid = body['session_id']
        name = body['name'].strip()
        try:
            s = get_session(sid)
        except KeyError:
            return bad(handler, 'Session not found', 404)
        # Resolve personality from config.yaml agent.personalities section
        # (matches hermes-agent CLI behavior)
        prompt = ''
        if name:
            from api.config import reload_config as _reload_cfg2
            _reload_cfg2()  # pick up config changes without restart
            from api.config import get_config as _get_cfg2
            _cfg2 = _get_cfg2()
            agent_cfg = _cfg2.get('agent', {})
            raw_personalities = agent_cfg.get('personalities', {})
            if not isinstance(raw_personalities, dict) or name not in raw_personalities:
                return bad(handler, f'Personality "{name}" not found in config.yaml', 404)
            value = raw_personalities[name]
            # Resolve prompt using the same logic as hermes-agent cli.py
            if isinstance(value, dict):
                parts = [value.get('system_prompt', '') or value.get('prompt', '')]
                if value.get('tone'):
                    parts.append(f'Tone: {value["tone"]}')
                if value.get('style'):
                    parts.append(f'Style: {value["style"]}')
                prompt = '\n'.join(p for p in parts if p)
            else:
                prompt = str(value)
        s.personality = name if name else None
        s.save()
        return j(handler, {'ok': True, 'personality': s.personality, 'prompt': prompt})

    # ── Character management (POST) ──
    if parsed.path.startswith('/api/characters/'):
        if parsed.path == '/api/characters/create':
            return _handle_character_create(handler, body)
        elif parsed.path == '/api/characters/delete' or parsed.path.endswith('/delete'):
            return _handle_character_delete(handler, body, parsed)
        elif parsed.path == '/api/characters/switch' or parsed.path.endswith('/switch'):
            # Match both /api/characters/switch and /api/characters/{id}/switch
            return _handle_character_switch(handler, body, parsed)
        elif parsed.path == '/api/characters/update' or parsed.path.endswith('/update'):
            # Match both /api/characters/update and /api/characters/{id}/update
            return _handle_character_update(handler, body, parsed)
        elif parsed.path.endswith('/open-folder'):
            # Match /api/characters/{id}/open-folder
            return _handle_character_open_folder(handler, body, parsed)

    if parsed.path == '/api/session/update':
        try: require(body, 'session_id')
        except ValueError as e: return bad(handler, str(e))
        try: s = get_session(body['session_id'])
        except KeyError: return bad(handler, 'Session not found', 404)
        new_ws = str(Path(body.get('workspace', s.workspace)).expanduser().resolve())
        s.workspace = new_ws; s.model = body.get('model', s.model); s.save()
        set_last_workspace(new_ws)
        return j(handler, {'session': s.compact() | {'messages': s.messages}})

    if parsed.path == '/api/session/delete':
        sid = body.get('session_id', '')
        if not sid: return bad(handler, 'session_id is required')
        # Delete from WebUI session store
        with LOCK: SESSIONS.pop(sid, None)
        p = SESSION_DIR / f'{sid}.json'
        try: p.unlink(missing_ok=True)
        except Exception: pass
        try: SESSION_INDEX_FILE.unlink(missing_ok=True)
        except Exception: pass
        # Also delete from CLI state.db (for CLI sessions shown in sidebar)
        try:
            from api.models import delete_cli_session
            delete_cli_session(sid)
        except Exception: pass
        return j(handler, {'ok': True})

    if parsed.path == '/api/session/clear':
        try: require(body, 'session_id')
        except ValueError as e: return bad(handler, str(e))
        try: s = get_session(body['session_id'])
        except KeyError: return bad(handler, 'Session not found', 404)
        s.messages = []; s.tool_calls = []; s.title = 'Untitled'; s.save()
        return j(handler, {'ok': True, 'session': s.compact()})

    if parsed.path == '/api/session/truncate':
        try: require(body, 'session_id')
        except ValueError as e: return bad(handler, str(e))
        if body.get('keep_count') is None:
            return bad(handler, 'Missing required field(s): keep_count')
        try: s = get_session(body['session_id'])
        except KeyError: return bad(handler, 'Session not found', 404)
        keep = int(body['keep_count'])
        s.messages = s.messages[:keep]; s.save()
        return j(handler, {'ok': True, 'session': s.compact() | {'messages': s.messages}})

    if parsed.path == '/api/chat/start':
        return _handle_chat_start(handler, body)

    # ── LLM config POST routes ─────────────────────────────────────────────────
    if parsed.path == '/api/llm-config/save':
        return _handle_llm_config_save(handler, body)

    if parsed.path == '/api/llm-config/test':
        return _handle_llm_config_test(handler, body)

    if parsed.path == '/api/chat':
        return _handle_chat_sync(handler, body)

    # ── Cron API (POST) ──
    if parsed.path == '/api/crons/create':
        return _handle_cron_create(handler, body)

    if parsed.path == '/api/crons/update':
        return _handle_cron_update(handler, body)

    if parsed.path == '/api/crons/delete':
        return _handle_cron_delete(handler, body)

    if parsed.path == '/api/crons/run':
        return _handle_cron_run(handler, body)

    if parsed.path == '/api/crons/pause':
        return _handle_cron_pause(handler, body)

    if parsed.path == '/api/crons/resume':
        return _handle_cron_resume(handler, body)

    # ── File ops (POST) ──
    if parsed.path == '/api/file/delete':
        return _handle_file_delete(handler, body)

    if parsed.path == '/api/file/save':
        return _handle_file_save(handler, body)

    if parsed.path == '/api/file/create':
        return _handle_file_create(handler, body)

    if parsed.path == '/api/file/rename':
        return _handle_file_rename(handler, body)

    if parsed.path == '/api/file/create-dir':
        return _handle_create_dir(handler, body)

    # ── Workspace management (POST) ──
    if parsed.path == '/api/workspaces/add':
        return _handle_workspace_add(handler, body)

    if parsed.path == '/api/workspaces/remove':
        return _handle_workspace_remove(handler, body)

    if parsed.path == '/api/workspaces/rename':
        return _handle_workspace_rename(handler, body)

    # ── Approval (POST) ──
    if parsed.path == '/api/approval/respond':
        return _handle_approval_respond(handler, body)

    # ── Skills (POST) ──
    if parsed.path == '/api/skills/save':
        return _handle_skill_save(handler, body)

    if parsed.path == '/api/skills/delete':
        return _handle_skill_delete(handler, body)

    # ── Memory (POST) ──
    if parsed.path == '/api/memory/write':
        return _handle_memory_write(handler, body)

    # ── Profile API (POST) ──
    if parsed.path == '/api/profile/switch':
        name = body.get('name', '').strip()
        if not name: return bad(handler, 'name is required')
        try:
            from api.profiles import switch_profile
            result = switch_profile(name)
            return j(handler, result)
        except (ValueError, FileNotFoundError) as e:
            return bad(handler, _sanitize_error(e), 404)
        except RuntimeError as e:
            return bad(handler, str(e), 409)

    if parsed.path == '/api/profile/create':
        name = body.get('name', '').strip()
        if not name: return bad(handler, 'name is required')
        import re as _re
        if not _re.match(r'^[a-z0-9][a-z0-9_-]{0,63}$', name):
            return bad(handler, 'Invalid profile name: lowercase letters, numbers, hyphens, underscores only')
        clone_from = body.get('clone_from')
        if clone_from is not None:
            clone_from = str(clone_from).strip()
            if not _re.match(r'^[a-z0-9][a-z0-9_-]{0,63}$', clone_from):
                return bad(handler, 'Invalid clone_from name')
        try:
            from api.profiles import create_profile_api
            result = create_profile_api(
                name,
                clone_from=clone_from,
                clone_config=bool(body.get('clone_config', False)),
            )
            return j(handler, {'ok': True, 'profile': result})
        except (ValueError, FileExistsError, RuntimeError) as e:
            return bad(handler, str(e))

    if parsed.path == '/api/profile/delete':
        name = body.get('name', '').strip()
        if not name: return bad(handler, 'name is required')
        try:
            from api.profiles import delete_profile_api
            result = delete_profile_api(name)
            return j(handler, result)
        except (ValueError, FileNotFoundError) as e:
            return bad(handler, _sanitize_error(e))
        except RuntimeError as e:
            return bad(handler, str(e), 409)

    # ── Settings (POST) ──
    if parsed.path == '/api/settings':
        if 'bot_name' in body:
            body['bot_name'] = (str(body['bot_name']) or '').strip() or 'Hermes'
        saved = save_settings(body)
        saved.pop('password_hash', None)  # never expose hash to client
        return j(handler, saved)

    # ── Session pin (POST) ──
    if parsed.path == '/api/session/pin':
        try: require(body, 'session_id')
        except ValueError as e: return bad(handler, str(e))
        try: s = get_session(body['session_id'])
        except KeyError: return bad(handler, 'Session not found', 404)
        s.pinned = bool(body.get('pinned', True))
        s.save()
        return j(handler, {'ok': True, 'session': s.compact()})

    # ── Session archive (POST) ──
    if parsed.path == '/api/session/archive':
        try: require(body, 'session_id')
        except ValueError as e: return bad(handler, str(e))
        try: s = get_session(body['session_id'])
        except KeyError: return bad(handler, 'Session not found', 404)
        s.archived = bool(body.get('archived', True))
        s.save()
        return j(handler, {'ok': True, 'session': s.compact()})

    # ── Session move to project (POST) ──
    if parsed.path == '/api/session/move':
        try: require(body, 'session_id')
        except ValueError as e: return bad(handler, str(e))
        try: s = get_session(body['session_id'])
        except KeyError: return bad(handler, 'Session not found', 404)
        s.project_id = body.get('project_id') or None
        s.save()
        return j(handler, {'ok': True, 'session': s.compact()})

    # ── Project CRUD (POST) ──
    if parsed.path == '/api/projects/create':
        try: require(body, 'name')
        except ValueError as e: return bad(handler, str(e))
        import re as _re
        name = body['name'].strip()[:128]
        if not name: return bad(handler, 'name required')
        color = body.get('color')
        if color and not _re.match(r'^#[0-9a-fA-F]{3,8}$', color):
            return bad(handler, 'Invalid color format')
        projects = load_projects()
        proj = {'project_id': uuid.uuid4().hex[:12], 'name': name, 'color': color, 'created_at': time.time()}
        projects.append(proj)
        save_projects(projects)
        return j(handler, {'ok': True, 'project': proj})

    if parsed.path == '/api/projects/rename':
        try: require(body, 'project_id', 'name')
        except ValueError as e: return bad(handler, str(e))
        import re as _re
        projects = load_projects()
        proj = next((p for p in projects if p['project_id'] == body['project_id']), None)
        if not proj: return bad(handler, 'Project not found', 404)
        proj['name'] = body['name'].strip()[:128]
        if 'color' in body:
            color = body['color']
            if color and not _re.match(r'^#[0-9a-fA-F]{3,8}$', color):
                return bad(handler, 'Invalid color format')
            proj['color'] = color
        save_projects(projects)
        return j(handler, {'ok': True, 'project': proj})

    if parsed.path == '/api/projects/delete':
        try: require(body, 'project_id')
        except ValueError as e: return bad(handler, str(e))
        projects = load_projects()
        proj = next((p for p in projects if p['project_id'] == body['project_id']), None)
        if not proj: return bad(handler, 'Project not found', 404)
        projects = [p for p in projects if p['project_id'] != body['project_id']]
        save_projects(projects)
        # Unassign all sessions that belonged to this project
        if SESSION_INDEX_FILE.exists():
            try:
                index = json.loads(SESSION_INDEX_FILE.read_text(encoding='utf-8'))
                for entry in index:
                    if entry.get('project_id') == body['project_id']:
                        try:
                            s = get_session(entry['session_id'])
                            s.project_id = None
                            s.save()
                        except Exception:
                            pass
            except Exception:
                pass
        return j(handler, {'ok': True})

    # ── Session import from JSON (POST) ──
    if parsed.path == '/api/session/import':
        return _handle_session_import(handler, body)

    # ── Self-update (POST) ──
    if parsed.path == '/api/updates/apply':
        target = body.get('target', '')
        if target not in ('webui', 'agent'):
            return bad(handler, 'target must be "webui" or "agent"')
        from api.updates import apply_update
        return j(handler, apply_update(target))

    # ── CLI session import (POST) ──
    if parsed.path == '/api/session/import_cli':
        return _handle_session_import_cli(handler, body)

    # ── Auth endpoints (POST) ──
    if parsed.path == '/api/auth/login':
        from api.auth import verify_password, create_session, set_auth_cookie, is_auth_enabled
        from api.auth import _check_login_rate, _record_login_attempt
        if not is_auth_enabled():
            return j(handler, {'ok': True, 'message': 'Auth not enabled'})
        client_ip = handler.client_address[0]
        if not _check_login_rate(client_ip):
            return j(handler, {'error': 'Too many attempts. Try again in a minute.'}, status=429)
        password = body.get('password', '')
        if not verify_password(password):
            _record_login_attempt(client_ip)
            return bad(handler, 'Invalid password', 401)
        cookie_val = create_session()
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/json')
        handler.send_header('Cache-Control', 'no-store')
        _security_headers(handler)
        set_auth_cookie(handler, cookie_val)
        handler.end_headers()
        handler.wfile.write(json.dumps({'ok': True}).encode())
        return True

    if parsed.path == '/api/auth/logout':
        from api.auth import clear_auth_cookie, invalidate_session, parse_cookie
        cookie_val = parse_cookie(handler)
        if cookie_val:
            invalidate_session(cookie_val)
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/json')
        handler.send_header('Cache-Control', 'no-store')
        _security_headers(handler)
        clear_auth_cookie(handler)
        handler.end_headers()
        handler.wfile.write(json.dumps({'ok': True}).encode())
        return True

    return False  # 404


# ── GET route helpers ─────────────────────────────────────────────────────────

def _serve_static(handler, parsed):
    static_root = (Path(__file__).parent.parent / 'static').resolve()
    # Strip the leading '/static/' prefix, then resolve and sandbox
    rel = parsed.path[len('/static/'):]
    static_file = (static_root / rel).resolve()
    try:
        static_file.relative_to(static_root)
    except ValueError:
        return j(handler, {'error': 'not found'}, status=404)
    if not static_file.exists() or not static_file.is_file():
        return j(handler, {'error': 'not found'}, status=404)
    ext = static_file.suffix.lower()
    ct = {'css': 'text/css', 'js': 'application/javascript',
          'html': 'text/html'}.get(ext.lstrip('.'), 'text/plain')
    handler.send_response(200)
    handler.send_header('Content-Type', f'{ct}; charset=utf-8')
    handler.send_header('Cache-Control', 'no-store')
    raw = static_file.read_bytes()
    handler.send_header('Content-Length', str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)
    return True


def _handle_session_export(handler, parsed):
    sid = parse_qs(parsed.query).get('session_id', [''])[0]
    if not sid: return bad(handler, 'session_id is required')
    try: s = get_session(sid)
    except KeyError: return bad(handler, 'Session not found', 404)
    payload = json.dumps(s.__dict__, ensure_ascii=False, indent=2)
    handler.send_response(200)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Disposition', f'attachment; filename="hermes-{sid}.json"')
    handler.send_header('Content-Length', str(len(payload.encode('utf-8'))))
    handler.send_header('Cache-Control', 'no-store')
    handler.end_headers()
    handler.wfile.write(payload.encode('utf-8'))
    return True


def _handle_sessions_search(handler, parsed):
    qs = parse_qs(parsed.query)
    q = qs.get('q', [''])[0].lower().strip()
    content_search = qs.get('content', ['1'])[0] == '1'
    depth = int(qs.get('depth', ['5'])[0])
    if not q: return j(handler, {'sessions': all_sessions()})
    results = []
    for s in all_sessions():
        title_match = q in (s.get('title') or '').lower()
        if title_match:
            results.append(dict(s, match_type='title'))
            continue
        if content_search:
            try:
                sess = get_session(s['session_id'])
                msgs = sess.messages[:depth] if depth else sess.messages
                for m in msgs:
                    c = m.get('content') or ''
                    if isinstance(c, list):
                        c = ' '.join(p.get('text', '') for p in c
                                     if isinstance(p, dict) and p.get('type') == 'text')
                    if q in str(c).lower():
                        results.append(dict(s, match_type='content'))
                        break
            except (KeyError, Exception):
                pass
    return j(handler, {'sessions': results, 'query': q, 'count': len(results)})


def _handle_list_dir(handler, parsed):
    qs = parse_qs(parsed.query)
    sid = qs.get('session_id', [''])[0]
    if not sid: return bad(handler, 'session_id is required')
    try:
        s = get_session(sid)
        workspace = s.workspace
    except KeyError:
        # Fallback for CLI sessions not loaded in WebUI memory
        try:
            cli_meta = None
            for cs in get_cli_sessions():
                if cs['session_id'] == sid:
                    cli_meta = cs
                    break
            if not cli_meta:
                return bad(handler, 'Session not found', 404)
            workspace = cli_meta.get('workspace', '')
        except Exception:
            return bad(handler, 'Session not found', 404)
    try:
        return j(handler, {
            'entries': list_dir(Path(workspace), qs.get('path', ['.'])[0]),
            'path': qs.get('path', ['.'])[0],
        })
    except (FileNotFoundError, ValueError) as e:
        return bad(handler, _sanitize_error(e), 404)


def _handle_sse_stream(handler, parsed):
    stream_id = parse_qs(parsed.query).get('stream_id', [''])[0]
    q = STREAMS.get(stream_id)
    if q is None:
        print(f"[webui] SSE stream not found: {stream_id}", flush=True)
        return j(handler, {'error': 'stream not found'}, status=404)
    print(f"[webui] SSE streaming started: {stream_id}", flush=True)
    handler.send_response(200)
    handler.send_header('Content-Type', 'text/event-stream; charset=utf-8')
    handler.send_header('Cache-Control', 'no-cache')
    handler.send_header('X-Accel-Buffering', 'no')
    handler.send_header('Connection', 'keep-alive')
    handler.end_headers()
    try:
        while True:
            try:
                event, data = q.get(timeout=30)
                print(f"[webui] SSE dequeued: {event}", flush=True)
            except queue.Empty:
                print(f"[webui] SSE timeout (no events in 30s)", flush=True)
                handler.wfile.write(b': heartbeat\n\n')
                handler.wfile.flush()
                continue
            _sse(handler, event, data)
            if event in ('done', 'error', 'cancel'):
                print(f"[webui] SSE stream ended: {event}", flush=True)
                break
    except (BrokenPipeError, ConnectionResetError):
        print(f"[webui] SSE connection error", flush=True)
    return True


def _handle_file_raw(handler, parsed):
    qs = parse_qs(parsed.query)
    sid = qs.get('session_id', [''])[0]
    if not sid: return bad(handler, 'session_id is required')
    try: s = get_session(sid)
    except KeyError: return bad(handler, 'Session not found', 404)
    rel = qs.get('path', [''])[0]
    force_download = qs.get('download', [''])[0] == '1'
    target = safe_resolve(Path(s.workspace), rel)
    if not target.exists() or not target.is_file():
        return j(handler, {'error': 'not found'}, status=404)
    ext = target.suffix.lower()
    mime = MIME_MAP.get(ext, 'application/octet-stream')
    raw_bytes = target.read_bytes()
    import urllib.parse as _up
    safe_name = _up.quote(target.name, safe='')
    handler.send_response(200)
    handler.send_header('Content-Type', mime)
    handler.send_header('Content-Length', str(len(raw_bytes)))
    handler.send_header('Cache-Control', 'no-store')
    # Security: force download for dangerous MIME types to prevent XSS
    dangerous_types = {'text/html', 'application/xhtml+xml', 'image/svg+xml'}
    if force_download or mime in dangerous_types:
        handler.send_header('Content-Disposition',
            f'attachment; filename="{target.name}"; filename*=UTF-8\'\'{safe_name}')
    else:
        handler.send_header('Content-Disposition',
            f'inline; filename="{target.name}"; filename*=UTF-8\'\'{safe_name}')
    handler.end_headers()
    handler.wfile.write(raw_bytes)
    return True


def _handle_file_read(handler, parsed):
    qs = parse_qs(parsed.query)
    sid = qs.get('session_id', [''])[0]
    if not sid: return bad(handler, 'session_id is required')
    try: s = get_session(sid)
    except KeyError: return bad(handler, 'Session not found', 404)
    rel = qs.get('path', [''])[0]
    if not rel: return bad(handler, 'path is required')
    try: return j(handler, read_file_content(Path(s.workspace), rel))
    except (FileNotFoundError, ValueError) as e: return bad(handler, _sanitize_error(e), 404)


def _handle_approval_pending(handler, parsed):
    sid = parse_qs(parsed.query).get('session_id', [''])[0]
    if has_pending(sid):
        with _lock:
            p = dict(_pending.get(sid, {}))
        return j(handler, {'pending': p})
    return j(handler, {'pending': None})


def _handle_approval_inject(handler, parsed):
    """Inject a fake pending approval -- loopback-only, used by automated tests."""
    qs = parse_qs(parsed.query)
    sid = qs.get('session_id', [''])[0]
    key = qs.get('pattern_key', ['test_pattern'])[0]
    cmd = qs.get('command', ['rm -rf /tmp/test'])[0]
    if sid:
        submit_pending(sid, {
            'command': cmd, 'pattern_key': key,
            'pattern_keys': [key], 'description': 'test pattern',
        })
        return j(handler, {'ok': True, 'session_id': sid})
    return j(handler, {'error': 'session_id required'}, status=400)


def _handle_cron_output(handler, parsed):
    from cron.jobs import OUTPUT_DIR as CRON_OUT
    qs = parse_qs(parsed.query)
    job_id = qs.get('job_id', [''])[0]
    limit = int(qs.get('limit', ['5'])[0])
    if not job_id: return j(handler, {'error': 'job_id required'}, status=400)
    out_dir = CRON_OUT / job_id
    outputs = []
    if out_dir.exists():
        files = sorted(out_dir.glob('*.md'), reverse=True)[:limit]
        for f in files:
            try:
                txt = f.read_text(encoding='utf-8', errors='replace')
                outputs.append({'filename': f.name, 'content': txt[:8000]})
            except Exception:
                pass
    return j(handler, {'job_id': job_id, 'outputs': outputs})


def _handle_cron_recent(handler, parsed):
    """Return cron jobs that have completed since a given timestamp."""
    import datetime
    qs = parse_qs(parsed.query)
    since = float(qs.get('since', ['0'])[0])
    try:
        from cron.jobs import list_jobs
        jobs = list_jobs(include_disabled=True)
        completions = []
        for job in jobs:
            last_run = job.get('last_run_at')
            if not last_run:
                continue
            if isinstance(last_run, str):
                try:
                    ts = datetime.datetime.fromisoformat(last_run.replace('Z', '+00:00')).timestamp()
                except (ValueError, TypeError):
                    continue
            else:
                ts = float(last_run)
            if ts > since:
                completions.append({
                    'job_id': job.get('id', ''),
                    'name': job.get('name', 'Unknown'),
                    'status': job.get('last_status', 'unknown'),
                    'completed_at': ts,
                })
        return j(handler, {'completions': completions, 'since': since})
    except ImportError:
        return j(handler, {'completions': [], 'since': since})


def _handle_memory_read(handler):
    try:
        from api.profiles import get_active_hermes_home
        mem_dir = get_active_hermes_home() / 'memories'
    except ImportError:
        mem_dir = Path.home() / '.hermes' / 'memories'
    mem_file = mem_dir / 'MEMORY.md'
    user_file = mem_dir / 'USER.md'
    memory = mem_file.read_text(encoding='utf-8', errors='replace') if mem_file.exists() else ''
    user = user_file.read_text(encoding='utf-8', errors='replace') if user_file.exists() else ''
    return j(handler, {
        'memory': memory, 'user': user,
        'memory_path': str(mem_file), 'user_path': str(user_file),
        'memory_mtime': mem_file.stat().st_mtime if mem_file.exists() else None,
        'user_mtime': user_file.stat().st_mtime if user_file.exists() else None,
    })


# ── POST route helpers ────────────────────────────────────────────────────────

def _handle_sessions_cleanup(handler, body, zero_only=False):
    cleaned = 0
    for p in SESSION_DIR.glob('*.json'):
        if p.name.startswith('_'): continue
        try:
            s = Session.load(p.stem)
            if zero_only:
                should_delete = s and len(s.messages) == 0
            else:
                should_delete = s and s.title == 'Untitled' and len(s.messages) == 0
            if should_delete:
                with LOCK: SESSIONS.pop(p.stem, None)
                p.unlink(missing_ok=True)
                cleaned += 1
        except Exception:
            pass
    if SESSION_INDEX_FILE.exists():
        SESSION_INDEX_FILE.unlink(missing_ok=True)
    return j(handler, {'ok': True, 'cleaned': cleaned})


def _handle_chat_start(handler, body):
    try: require(body, 'session_id')
    except ValueError as e: return bad(handler, str(e))
    try: s = get_session(body['session_id'])
    except KeyError: return bad(handler, 'Session not found', 404)
    msg = str(body.get('message', '')).strip()
    if not msg: return bad(handler, 'message is required')
    attachments = [str(a) for a in (body.get('attachments') or [])][:20]
    workspace = str(Path(body.get('workspace') or s.workspace).expanduser().resolve())
    model = body.get('model') or s.model
    s.workspace = workspace; s.model = model; s.save()
    set_last_workspace(workspace)
    stream_id = uuid.uuid4().hex
    q = queue.Queue()
    with STREAMS_LOCK: STREAMS[stream_id] = q
    thr = threading.Thread(
        target=_run_agent_streaming,
        args=(s.session_id, msg, model, workspace, stream_id, attachments),
        daemon=True,
    )
    thr.start()
    return j(handler, {'stream_id': stream_id, 'session_id': s.session_id})


def _handle_llm_config_save(handler, body):
    """POST /api/llm-config/save — Save LLM provider configuration."""
    from api.llm_config import save_llm_config, load_llm_config
    llm_cfg = dict(body.get('llm', body))  # accept either {llm:{...}} or flat {...}
    if not llm_cfg.get('provider'):
        return bad(handler, 'provider is required')
    # If key is empty and keep_existing_key is set, preserve the stored key
    keep_key = llm_cfg.pop('_keep_existing_key', False)
    if keep_key and not llm_cfg.get('api_key', '').strip():
        existing = load_llm_config()
        llm_cfg['api_key'] = existing.get('api_key', '')
    save_llm_config(llm_cfg)
    return j(handler, {'ok': True})


def _handle_llm_config_test(handler, body):
    """POST /api/llm-config/test — Test LLM connection."""
    from api.llm_config import test_connection, load_llm_config
    llm_cfg = dict(body.get('llm', body))
    keep_key = llm_cfg.pop('_keep_existing_key', False)
    if keep_key and not llm_cfg.get('api_key', '').strip():
        existing = load_llm_config()
        llm_cfg['api_key'] = existing.get('api_key', '')
    result = test_connection(llm_cfg)
    return j(handler, result)


def _handle_chat_sync(handler, body):
    """Fallback synchronous chat endpoint (POST /api/chat). Used by desktop app."""
    from api.llm_config import is_configured, load_llm_config, chat_with_config

    # ── 优先使用用户配置的 LLM (user/config.json) ────────────────────────────
    if is_configured():
        return _handle_chat_direct(handler, body)

    from api.config import _get_session_agent_lock
    session_id = body.get('session_id', '')
    if not session_id:
        return bad(handler, 'session_id is required')
    try:
        s = get_session(session_id)
    except KeyError:
        return bad(handler, f'Session not found: {session_id}', 404)
    msg = str(body.get('message', '')).strip()
    if not msg: return j(handler, {'error': 'empty message'}, status=400)
    workspace = Path(body.get('workspace') or s.workspace).expanduser().resolve()
    s.workspace = str(workspace); s.model = body.get('model') or s.model
    from api.streaming import _ENV_LOCK
    with _ENV_LOCK:
        old_cwd = os.environ.get('TERMINAL_CWD')
        os.environ['TERMINAL_CWD'] = str(workspace)
        old_exec_ask = os.environ.get('HERMES_EXEC_ASK')
        old_session_key = os.environ.get('HERMES_SESSION_KEY')
        os.environ['HERMES_EXEC_ASK'] = '1'
        os.environ['HERMES_SESSION_KEY'] = s.session_id
    try:
        from run_agent import AIAgent
        with CHAT_LOCK:
            from api.config import resolve_model_provider
            _model, _provider, _base_url = resolve_model_provider(s.model)
            # Resolve API key via Hermes runtime provider (matches gateway behaviour)
            _api_key = None
            try:
                from hermes_cli.runtime_provider import resolve_runtime_provider
                _rt = resolve_runtime_provider(requested=_provider)
                _api_key = _rt.get("api_key")
                # Also use runtime provider/base_url if the webui config didn't resolve them
                if not _provider:
                    _provider = _rt.get("provider")
                if not _base_url:
                    _base_url = _rt.get("base_url")
            except Exception as _e:
                print(f"[webui] WARNING: resolve_runtime_provider failed: {_e}", flush=True)
            agent = AIAgent(model=_model, provider=_provider, base_url=_base_url,
                           api_key=_api_key, platform='cli', quiet_mode=True,
                           enabled_toolsets=CLI_TOOLSETS, session_id=s.session_id)
            workspace_ctx = f"[Workspace: {s.workspace}]\n"
            workspace_system_msg = (
                f"Active workspace at session start: {s.workspace}\n"
                "Every user message is prefixed with [Workspace: /absolute/path] indicating the "
                "workspace the user has selected in the web UI at the time they sent that message. "
                "This tag is the single authoritative source of the active workspace and updates "
                "with every message. It overrides any prior workspace mentioned in this system "
                "prompt, memory, or conversation history. Always use the value from the most recent "
                "[Workspace: ...] tag as your default working directory for ALL file operations: "
                "write_file, read_file, search_files, terminal workdir, and patch. "
                "Never fall back to a hardcoded path when this tag is present."
            )
            from api.streaming import _sanitize_messages_for_api
            result = agent.run_conversation(
                user_message=workspace_ctx + msg,
                system_message=workspace_system_msg,
                conversation_history=_sanitize_messages_for_api(s.messages),
                task_id=s.session_id,
                persist_user_message=msg,
            )
    finally:
        with _ENV_LOCK:
            if old_cwd is None: os.environ.pop('TERMINAL_CWD', None)
            else: os.environ['TERMINAL_CWD'] = old_cwd
            if old_exec_ask is None: os.environ.pop('HERMES_EXEC_ASK', None)
            else: os.environ['HERMES_EXEC_ASK'] = old_exec_ask
            if old_session_key is None: os.environ.pop('HERMES_SESSION_KEY', None)
            else: os.environ['HERMES_SESSION_KEY'] = old_session_key
    s.messages = result.get('messages') or s.messages
    s.title = title_from(s.messages, s.title); s.save()
    # Sync to state.db for /insights (opt-in setting)
    try:
        if load_settings().get('sync_to_insights'):
            from api.state_sync import sync_session_usage
            sync_session_usage(
                session_id=s.session_id,
                input_tokens=s.input_tokens or 0,
                output_tokens=s.output_tokens or 0,
                estimated_cost=s.estimated_cost,
                model=s.model,
                title=s.title,
                message_count=len(s.messages),
            )
    except Exception:
        pass
    return j(handler, {
        'answer': result.get('final_response') or '',
        'status': 'done' if result.get('completed', True) else 'partial',
        'session': s.compact() | {'messages': s.messages},
        'result': {k: v for k, v in result.items() if k != 'messages'},
    })


def _handle_chat_direct(handler, body):
    """Direct LLM chat using user/config.json — bypasses Hermes agent."""
    from api.llm_config import load_llm_config, chat_with_config

    session_id = body.get('session_id', '')
    if not session_id:
        return bad(handler, 'session_id is required')
    try:
        s = get_session(session_id)
    except KeyError:
        return bad(handler, f'Session not found: {session_id}', 404)

    msg = str(body.get('message', '')).strip()
    if not msg:
        return j(handler, {'error': 'empty message'}, status=400)

    # 读取当前角色的 system_prompt
    system_prompt = ''
    try:
        active_char = _load_active_character()
        if active_char:
            char_cfg = _load_character_config(active_char)
            system_prompt = (char_cfg or {}).get('system_prompt', '')
    except Exception as e:
        print(f'[direct_chat] Could not load character system_prompt: {e}', flush=True)

    cfg = load_llm_config()
    try:
        reply = chat_with_config(
            message=msg,
            history=s.messages,
            system_prompt=system_prompt,
            cfg=cfg,
        )
    except Exception as e:
        print(f'[direct_chat] LLM call failed: {e}', flush=True)
        return j(handler, {'error': f'LLM error: {e}'}, status=500)

    # 保存消息到 session
    s.messages.append({'role': 'user', 'content': msg})
    s.messages.append({'role': 'assistant', 'content': reply})
    s.title = title_from(s.messages, s.title)
    s.save()

    return j(handler, {
        'answer': reply,
        'status': 'done',
        'session': s.compact(),
    })


def _handle_cron_create(handler, body):
    try: require(body, 'prompt', 'schedule')
    except ValueError as e: return bad(handler, str(e))
    try:
        from cron.jobs import create_job
        job = create_job(
            prompt=body['prompt'], schedule=body['schedule'],
            name=body.get('name') or None, deliver=body.get('deliver') or 'local',
            skills=body.get('skills') or [], model=body.get('model') or None,
        )
        return j(handler, {'ok': True, 'job': job})
    except Exception as e:
        return j(handler, {'error': str(e)}, status=400)


def _handle_cron_update(handler, body):
    try: require(body, 'job_id')
    except ValueError as e: return bad(handler, str(e))
    from cron.jobs import update_job
    updates = {k: v for k, v in body.items() if k != 'job_id' and v is not None}
    job = update_job(body['job_id'], updates)
    if not job: return bad(handler, 'Job not found', 404)
    return j(handler, {'ok': True, 'job': job})


def _handle_cron_delete(handler, body):
    try: require(body, 'job_id')
    except ValueError as e: return bad(handler, str(e))
    from cron.jobs import remove_job
    ok = remove_job(body['job_id'])
    if not ok: return bad(handler, 'Job not found', 404)
    return j(handler, {'ok': True, 'job_id': body['job_id']})


def _handle_cron_run(handler, body):
    job_id = body.get('job_id', '')
    if not job_id: return bad(handler, 'job_id required')
    from cron.jobs import get_job
    from cron.scheduler import run_job
    job = get_job(job_id)
    if not job: return bad(handler, 'Job not found', 404)
    threading.Thread(target=run_job, args=(job,), daemon=True).start()
    return j(handler, {'ok': True, 'job_id': job_id, 'status': 'triggered'})


def _handle_cron_pause(handler, body):
    job_id = body.get('job_id', '')
    if not job_id: return bad(handler, 'job_id required')
    from cron.jobs import pause_job
    result = pause_job(job_id, reason=body.get('reason'))
    if result: return j(handler, {'ok': True, 'job': result})
    return bad(handler, 'Job not found', 404)


def _handle_cron_resume(handler, body):
    job_id = body.get('job_id', '')
    if not job_id: return bad(handler, 'job_id required')
    from cron.jobs import resume_job
    result = resume_job(job_id)
    if result: return j(handler, {'ok': True, 'job': result})
    return bad(handler, 'Job not found', 404)


def _handle_file_delete(handler, body):
    try: require(body, 'session_id', 'path')
    except ValueError as e: return bad(handler, str(e))
    try: s = get_session(body['session_id'])
    except KeyError: return bad(handler, 'Session not found', 404)
    try:
        target = safe_resolve(Path(s.workspace), body['path'])
        if not target.exists(): return bad(handler, 'File not found', 404)
        if target.is_dir(): return bad(handler, 'Cannot delete directories via this endpoint')
        target.unlink()
        return j(handler, {'ok': True, 'path': body['path']})
    except (ValueError, PermissionError) as e: return bad(handler, _sanitize_error(e))


def _handle_file_save(handler, body):
    try: require(body, 'session_id', 'path')
    except ValueError as e: return bad(handler, str(e))
    try: s = get_session(body['session_id'])
    except KeyError: return bad(handler, 'Session not found', 404)
    try:
        target = safe_resolve(Path(s.workspace), body['path'])
        if not target.exists(): return bad(handler, 'File not found', 404)
        if target.is_dir(): return bad(handler, 'Cannot save: path is a directory')
        target.write_text(body.get('content', ''), encoding='utf-8')
        return j(handler, {'ok': True, 'path': body['path'], 'size': target.stat().st_size})
    except (ValueError, PermissionError) as e: return bad(handler, _sanitize_error(e))


def _handle_file_create(handler, body):
    try: require(body, 'session_id', 'path')
    except ValueError as e: return bad(handler, str(e))
    try: s = get_session(body['session_id'])
    except KeyError: return bad(handler, 'Session not found', 404)
    try:
        target = safe_resolve(Path(s.workspace), body['path'])
        if target.exists(): return bad(handler, 'File already exists')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body.get('content', ''), encoding='utf-8')
        return j(handler, {'ok': True, 'path': str(target.relative_to(Path(s.workspace)))})
    except (ValueError, PermissionError) as e: return bad(handler, _sanitize_error(e))


def _handle_file_rename(handler, body):
    try: require(body, 'session_id', 'path', 'new_name')
    except ValueError as e: return bad(handler, str(e))
    try: s = get_session(body['session_id'])
    except KeyError: return bad(handler, 'Session not found', 404)
    try:
        source = safe_resolve(Path(s.workspace), body['path'])
        if not source.exists(): return bad(handler, 'File not found', 404)
        new_name = body['new_name'].strip()
        if not new_name or '/' in new_name or '..' in new_name:
            return bad(handler, 'Invalid file name')
        dest = source.parent / new_name
        if dest.exists(): return bad(handler, f'A file named "{new_name}" already exists')
        source.rename(dest)
        new_rel = str(dest.relative_to(Path(s.workspace)))
        return j(handler, {'ok': True, 'old_path': body['path'], 'new_path': new_rel})
    except (ValueError, PermissionError, OSError) as e: return bad(handler, _sanitize_error(e))


def _handle_create_dir(handler, body):
    try: require(body, 'session_id', 'path')
    except ValueError as e: return bad(handler, str(e))
    try: s = get_session(body['session_id'])
    except KeyError: return bad(handler, 'Session not found', 404)
    try:
        target = safe_resolve(Path(s.workspace), body['path'])
        if target.exists(): return bad(handler, 'Path already exists')
        target.mkdir(parents=True)
        return j(handler, {'ok': True, 'path': str(target.relative_to(Path(s.workspace)))})
    except (ValueError, PermissionError, OSError) as e: return bad(handler, _sanitize_error(e))


def _handle_workspace_add(handler, body):
    path_str = body.get('path', '').strip()
    name = body.get('name', '').strip()
    if not path_str: return bad(handler, 'path is required')
    p = Path(path_str).expanduser().resolve()
    if not p.exists(): return bad(handler, f'Path does not exist: {p}')
    if not p.is_dir(): return bad(handler, f'Path is not a directory: {p}')
    wss = load_workspaces()
    if any(w['path'] == str(p) for w in wss):
        return bad(handler, 'Workspace already in list')
    wss.append({'path': str(p), 'name': name or p.name})
    save_workspaces(wss)
    return j(handler, {'ok': True, 'workspaces': wss})


def _handle_workspace_remove(handler, body):
    path_str = body.get('path', '').strip()
    if not path_str: return bad(handler, 'path is required')
    wss = load_workspaces()
    wss = [w for w in wss if w['path'] != path_str]
    save_workspaces(wss)
    return j(handler, {'ok': True, 'workspaces': wss})


def _handle_workspace_rename(handler, body):
    path_str = body.get('path', '').strip()
    name = body.get('name', '').strip()
    if not path_str or not name: return bad(handler, 'path and name are required')
    wss = load_workspaces()
    for w in wss:
        if w['path'] == path_str:
            w['name'] = name; break
    else:
        return bad(handler, 'Workspace not found', 404)
    save_workspaces(wss)
    return j(handler, {'ok': True, 'workspaces': wss})


def _handle_approval_respond(handler, body):
    sid = body.get('session_id', '')
    if not sid: return bad(handler, 'session_id is required')
    choice = body.get('choice', 'deny')
    if choice not in ('once', 'session', 'always', 'deny'):
        return bad(handler, f'Invalid choice: {choice}')
    # Pop the legacy polling-mode pending entry (no-op when gateway path is active).
    with _lock:
        pending = _pending.pop(sid, None)
    if pending:
        keys = pending.get('pattern_keys') or [pending.get('pattern_key', '')]
        if choice in ('once', 'session'):
            for k in keys: approve_session(sid, k)
        elif choice == 'always':
            for k in keys:
                approve_session(sid, k); approve_permanent(k)
            save_permanent_allowlist(_permanent_approved)
    # Unblock the agent thread waiting in the gateway approval queue.
    # This is the primary signal when streaming is active — the agent
    # thread is parked in entry.event.wait() and needs to be woken up.
    resolve_gateway_approval(sid, choice, resolve_all=False)
    return j(handler, {'ok': True, 'choice': choice})


def _handle_skill_save(handler, body):
    try: require(body, 'name', 'content')
    except ValueError as e: return bad(handler, str(e))
    skill_name = body['name'].strip().lower().replace(' ', '-')
    if not skill_name or '/' in skill_name or '..' in skill_name:
        return bad(handler, 'Invalid skill name')
    category = body.get('category', '').strip()
    if category and ('/' in category or '..' in category):
        return bad(handler, 'Invalid category')
    from tools.skills_tool import SKILLS_DIR
    if category:
        skill_dir = SKILLS_DIR / category / skill_name
    else:
        skill_dir = SKILLS_DIR / skill_name
    # Validate resolved path stays within SKILLS_DIR
    try:
        skill_dir.resolve().relative_to(SKILLS_DIR.resolve())
    except ValueError:
        return bad(handler, 'Invalid skill path')
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / 'SKILL.md'
    skill_file.write_text(body['content'], encoding='utf-8')
    return j(handler, {'ok': True, 'name': skill_name, 'path': str(skill_file)})


def _handle_skill_delete(handler, body):
    try: require(body, 'name')
    except ValueError as e: return bad(handler, str(e))
    from tools.skills_tool import SKILLS_DIR
    import shutil
    matches = list(SKILLS_DIR.rglob(f'{body["name"]}/SKILL.md'))
    if not matches: return bad(handler, 'Skill not found', 404)
    skill_dir = matches[0].parent
    shutil.rmtree(str(skill_dir))
    return j(handler, {'ok': True, 'name': body['name']})


def _handle_memory_write(handler, body):
    try: require(body, 'section', 'content')
    except ValueError as e: return bad(handler, str(e))
    try:
        from api.profiles import get_active_hermes_home
        mem_dir = get_active_hermes_home() / 'memories'
    except ImportError:
        mem_dir = Path.home() / '.hermes' / 'memories'
    mem_dir.mkdir(parents=True, exist_ok=True)
    section = body['section']
    if section == 'memory':
        target = mem_dir / 'MEMORY.md'
    elif section == 'user':
        target = mem_dir / 'USER.md'
    else:
        return bad(handler, 'section must be "memory" or "user"')
    target.write_text(body['content'], encoding='utf-8')
    return j(handler, {'ok': True, 'section': section, 'path': str(target)})


def _handle_session_import_cli(handler, body):
    """Import a single CLI session into the WebUI store."""
    try:
        require(body, 'session_id')
    except ValueError as e:
        return bad(handler, str(e))

    sid = str(body['session_id'])

    # Check if already imported — idempotent
    existing = Session.load(sid)
    if existing:
        return j(handler, {'session': existing.compact() | {
            'messages': existing.messages,
            'is_cli_session': True,
        }, 'imported': False})

    # Fetch messages from CLI store
    msgs = get_cli_session_messages(sid)
    if not msgs:
        return bad(handler, 'Session not found in CLI store', 404)

    # Derive title from first user message
    title = title_from(msgs, 'CLI Session')
    model = 'unknown'

    # Get profile and model from CLI session metadata
    profile = None
    for cs in get_cli_sessions():
        if cs['session_id'] == sid:
            profile = cs.get('profile')
            model = cs.get('model', 'unknown')
            break

    s = import_cli_session(sid, title, msgs, model, profile=profile)
    s.is_cli_session = True
    s._cli_origin = sid
    s.save()
    return j(handler, {
        'session': s.compact() | {
            'messages': msgs,
            'is_cli_session': True,
        },
        'imported': True,
    })


def _handle_session_import(handler, body):
    """Import a session from a JSON export. Creates a new session with a new ID."""
    if not body or not isinstance(body, dict):
        return bad(handler, 'Request body must be a JSON object')
    messages = body.get('messages')
    if not isinstance(messages, list):
        return bad(handler, 'JSON must contain a "messages" array')
    title = body.get('title', 'Imported session')
    workspace = body.get('workspace', str(DEFAULT_WORKSPACE))
    model = body.get('model', DEFAULT_MODEL)
    s = Session(
        title=title, workspace=workspace, model=model,
        messages=messages,
        tool_calls=body.get('tool_calls', []),
    )
    s.pinned = body.get('pinned', False)
    with LOCK:
        SESSIONS[s.session_id] = s
        SESSIONS.move_to_end(s.session_id)
        while len(SESSIONS) > SESSIONS_MAX:
            SESSIONS.popitem(last=False)
    s.save()
    return j(handler, {'ok': True, 'session': s.compact() | {'messages': s.messages}})


# ── Character management helpers ──────────────────────────────────────────────

def _get_characters_dir():
    """Get the characters directory, resolving it relative to the webui root."""
    try:
        from api.config import REPO_ROOT
        chars_dir = REPO_ROOT / 'user' / 'characters'
    except ImportError:
        chars_dir = Path(__file__).parent.parent / 'user' / 'characters'
    return chars_dir.resolve()


def _load_character_config(character_id):
    """Load a single character config file. Returns dict or None if not found."""
    config_file = _get_characters_dir() / character_id / 'config.json'
    if config_file.exists():
        try:
            return json.loads(config_file.read_text(encoding='utf-8'))
        except Exception:
            return None
    return None


def _save_character_config(character_id, config):
    """Save a character config file. Creates directory if needed."""
    char_dir = _get_characters_dir() / character_id
    char_dir.mkdir(parents=True, exist_ok=True)
    config_file = char_dir / 'config.json'
    config_file.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    return True


def _load_active_character():
    """Load the currently active character ID from a state file. Returns character_id or None."""
    state_file = _get_characters_dir().parent / '.active_character'
    if state_file.exists():
        try:
            return state_file.read_text(encoding='utf-8').strip()
        except Exception:
            return None
    return None


def _save_active_character(character_id):
    """Save the active character ID to a state file."""
    state_file = _get_characters_dir().parent / '.active_character'
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(character_id, encoding='utf-8')
    return True


def _handle_characters_list(handler):
    """GET /api/characters/list - List all available characters."""
    chars_dir = _get_characters_dir()
    characters = []
    active_char = _load_active_character()

    if chars_dir.exists() and chars_dir.is_dir():
        for char_dir in chars_dir.iterdir():
            if char_dir.is_dir():
                config = _load_character_config(char_dir.name)
                if config and isinstance(config, dict):
                    char_entry = {
                        'id': config.get('id', char_dir.name),
                        'name': config.get('name', 'Unknown'),
                        'description': config.get('description', ''),
                        'system_prompt': config.get('system_prompt', ''),
                        'frames_count': config.get('frames_count', 0),
                        'is_active': char_dir.name == active_char,
                    }
                    characters.append(char_entry)

    characters.sort(key=lambda c: c['name'])
    return j(handler, {
        'characters': characters,
        'active': active_char,
    })


def _handle_character_switch(handler, body, parsed):
    """POST /api/characters/{character_id}/switch - Switch to a different character."""
    # Extract character_id from path or body
    character_id = body.get('character_id', '').strip()

    # Try to extract from path (e.g., /api/characters/xuebao/switch)
    if not character_id:
        parts = parsed.path.strip('/').split('/')
        if len(parts) >= 3 and parts[0] == 'api' and parts[1] == 'characters':
            character_id = parts[2]

    if not character_id:
        return bad(handler, 'character_id is required')

    # Verify the character exists
    config = _load_character_config(character_id)
    if not config:
        return bad(handler, f'Character "{character_id}" not found', 404)

    # Save as active character
    _save_active_character(character_id)

    # 🆕 Switch Hermes profile to use this character's SOUL.md and MEMORY.md
    try:
        from api.profiles import switch_profile
        switch_profile(character_id)
        print(f"[webui] ✓ Switched Hermes profile to: {character_id}", flush=True)
    except Exception as e:
        print(f"[webui] ⚠️  Could not switch Hermes profile: {e}", flush=True)
        # Don't fail the API call if profile switch fails — character still switched

    # ── Ensure this character has a session, get or create one ──────────────────
    from api.models import Session
    session_id = config.get('session_id', '')
    if session_id:
        # Verify the session file exists
        session_file = SESSION_DIR / f'{session_id}.json'
        if not session_file.exists():
            session_id = ''  # Fall through to create new
    if not session_id:
        # Create a new session for this character
        session_id = uuid.uuid4().hex[:12]
        s = Session(session_id=session_id, profile=character_id)
        s.save()
        # Persist session_id back to character config
        config['session_id'] = session_id
        _save_character_config(character_id, config)
        print(f"[webui] ✓ Created new session {session_id} for character: {character_id}", flush=True)

    return j(handler, {
        'ok': True,
        'active': character_id,
        'session_id': session_id,
        'character': {
            'id': config.get('id', character_id),
            'name': config.get('name', 'Unknown'),
            'description': config.get('description', ''),
            'system_prompt': config.get('system_prompt', ''),
            'frames_count': config.get('frames_count', 0),
        }
    })


def _handle_character_update(handler, body, parsed):
    """POST /api/characters/{character_id}/update - Update character configuration."""
    # Extract character_id from path or body
    character_id = body.get('character_id', '').strip()

    # Try to extract from path (e.g., /api/characters/xuebao/update)
    if not character_id:
        parts = parsed.path.strip('/').split('/')
        if len(parts) >= 3 and parts[0] == 'api' and parts[1] == 'characters':
            character_id = parts[2]

    if not character_id:
        return bad(handler, 'character_id is required')

    # Load existing config
    config = _load_character_config(character_id)
    if not config:
        return bad(handler, f'Character "{character_id}" not found', 404)

    # Update allowed fields
    updatable_fields = ['name', 'description', 'system_prompt', 'session_id', 'frames_count']
    for field in updatable_fields:
        if field in body:
            config[field] = body[field]

    # Always preserve id
    config['id'] = character_id

    # Save updated config
    _save_character_config(character_id, config)

    return j(handler, {
        'ok': True,
        'character': {
            'id': config.get('id', character_id),
            'name': config.get('name', 'Unknown'),
            'description': config.get('description', ''),
            'system_prompt': config.get('system_prompt', ''),
            'session_id': config.get('session_id', ''),
            'frames_count': config.get('frames_count', 0),
        }
    })


def _setup_character_configs(character_id, char_dir, system_prompt):
    """
    自动为新建的人物配置所有必需的文件和目录。
    复制 config.yaml, auth.json, skills 等从 ~/.hermes/
    """
    import shutil

    hermes_home = Path.home() / '.hermes'

    try:
        # 1️⃣ 复制 config.yaml (Hermes 配置)
        hermes_config = hermes_home / 'config.yaml'
        if hermes_config.exists():
            shutil.copy2(str(hermes_config), str(char_dir / 'config.yaml'))
            print(f"[setup] ✓ 复制 config.yaml 到 {character_id}", flush=True)

        # 2️⃣ 复制 auth.json (API keys)
        hermes_auth = hermes_home / 'auth.json'
        if hermes_auth.exists():
            shutil.copy2(str(hermes_auth), str(char_dir / 'auth.json'))
            print(f"[setup] ✓ 复制 auth.json 到 {character_id}", flush=True)

        # 3️⃣ 复制 skills 目录
        hermes_skills = hermes_home / 'skills'
        if hermes_skills.exists():
            dest_skills = char_dir / 'skills'
            if dest_skills.exists():
                shutil.rmtree(str(dest_skills))
            shutil.copytree(str(hermes_skills), str(dest_skills))
            print(f"[setup] ✓ 复制 skills 目录到 {character_id}", flush=True)

        # 4️⃣ 复制 skills_disabled 目录
        hermes_skills_disabled = hermes_home / 'skills_disabled'
        if hermes_skills_disabled.exists():
            dest_skills_disabled = char_dir / 'skills_disabled'
            if dest_skills_disabled.exists():
                shutil.rmtree(str(dest_skills_disabled))
            shutil.copytree(str(hermes_skills_disabled), str(dest_skills_disabled))
            print(f"[setup] ✓ 复制 skills_disabled 目录到 {character_id}", flush=True)

        # 5️⃣ 创建 SOUL.md (角色性格定义)
        soul_file = char_dir / 'SOUL.md'
        if not soul_file.exists():
            soul_file.write_text(system_prompt if system_prompt else f"你是{character_id}，一个有趣的AI助手。\n")
            print(f"[setup] ✓ 创建 SOUL.md 到 {character_id}", flush=True)

        # 6️⃣ 创建 MEMORY.md (角色记忆库)
        memory_file = char_dir / 'MEMORY.md'
        if not memory_file.exists():
            memory_file.write_text(f"# {character_id} 的记忆库\n\n这里会存储与{character_id}的对话记忆和学习内容。\n")
            print(f"[setup] ✓ 创建 MEMORY.md 到 {character_id}", flush=True)

        # 7️⃣ 创建 USER.md (用户信息)
        user_file = char_dir / 'USER.md'
        if not user_file.exists():
            user_file.write_text(f"# 用户信息\n\n与{character_id}互动的用户相关信息。\n")
            print(f"[setup] ✓ 创建 USER.md 到 {character_id}", flush=True)

        # 8️⃣ 创建符号链接到 ~/.hermes/profiles/
        profiles_dir = hermes_home / 'profiles'
        profiles_dir.mkdir(exist_ok=True)
        profile_link = profiles_dir / character_id

        # 删除已存在的符号链接
        if profile_link.exists() or profile_link.is_symlink():
            os.unlink(str(profile_link))

        # 创建新的符号链接
        os.symlink(str(char_dir), str(profile_link))
        print(f"[setup] ✓ 创建符号链接 ~/.hermes/profiles/{character_id} → {char_dir}", flush=True)

        # 9️⃣ 创建必需的子目录
        for subdir in ['memories', 'sessions', 'logs', 'cron', 'webui_state']:
            (char_dir / subdir).mkdir(exist_ok=True)
        print(f"[setup] ✓ 创建子目录 (memories, sessions, logs, cron, webui_state)", flush=True)

        print(f"[setup] ✅ {character_id} 配置完成！", flush=True)
        return True

    except Exception as e:
        print(f"[setup] ❌ 配置 {character_id} 失败: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return False


def _handle_character_create(handler, body):
    """POST /api/characters/create - Create a new character."""
    # Validate required fields
    try:
        require(body, 'id', 'name')
    except ValueError as e:
        return bad(handler, str(e))

    character_id = str(body.get('id', '')).strip().lower()

    # Validate character_id format (alphanumeric, dash, underscore only)
    if not character_id or not _re.match(r'^[a-z0-9_-]+$', character_id):
        return bad(handler, 'Invalid character_id: use lowercase letters, numbers, hyphens, underscores only')

    # Check if character already exists
    chars_dir = _get_characters_dir()
    char_dir = chars_dir / character_id
    if char_dir.exists():
        return bad(handler, f'Character "{character_id}" already exists', 409)

    # Create new character config
    config = {
        'id': character_id,
        'name': str(body.get('name', '')).strip(),
        'description': str(body.get('description', '')).strip(),
        'system_prompt': str(body.get('system_prompt', '')).strip(),
        'session_id': '',
        'frames_count': 1,  # Default to 1 static frame
    }

    # Validate name is not empty
    if not config['name']:
        return bad(handler, 'name is required and cannot be empty')

    # Create character directory and save config
    try:
        char_dir.mkdir(parents=True, exist_ok=True)
        _save_character_config(character_id, config)

        # 🆕 自动配置 Hermes 相关文件 (config.yaml, auth.json, skills, SOUL.md 等)
        setup_success = _setup_character_configs(
            character_id,
            char_dir,
            config.get('system_prompt', '')
        )

        if not setup_success:
            print(f"[warn] 人物 {character_id} 创建了，但自动配置过程中出现警告", flush=True)
            # 继续返回成功，因为基本配置已完成

        return j(handler, {
            'ok': True,
            'character': {
                'id': config.get('id', character_id),
                'name': config.get('name', 'Unknown'),
                'description': config.get('description', ''),
                'system_prompt': config.get('system_prompt', ''),
                'session_id': config.get('session_id', ''),
                'frames_count': config.get('frames_count', 1),
            }
        })
    except Exception as e:
        return bad(handler, f'Failed to create character: {_sanitize_error(e)}', 500)


def _handle_character_delete(handler, body, parsed):
    """POST /api/characters/{character_id}/delete - Delete a character."""
    # Extract character_id from path or body
    character_id = body.get('character_id', '').strip()

    # Try to extract from path (e.g., /api/characters/newchar/delete)
    if not character_id:
        parts = parsed.path.strip('/').split('/')
        if len(parts) >= 3 and parts[0] == 'api' and parts[1] == 'characters':
            character_id = parts[2]

    if not character_id:
        return bad(handler, 'character_id is required')

    # Verify the character exists
    chars_dir = _get_characters_dir()
    char_dir = chars_dir / character_id
    if not char_dir.exists():
        return bad(handler, f'Character "{character_id}" not found', 404)

    # Prevent deleting active character
    active_char = _load_active_character()
    if active_char == character_id:
        return bad(handler, f'Cannot delete the active character. Switch to another character first.', 409)

    # Delete the character directory and all its contents
    try:
        import shutil
        shutil.rmtree(str(char_dir))
        return j(handler, {
            'ok': True,
            'deleted': character_id,
        })
    except Exception as e:
        return bad(handler, f'Failed to delete character: {_sanitize_error(e)}', 500)


def _handle_character_open_folder(handler, body, parsed):
    """POST /api/characters/{character_id}/open-folder - Open character folder in file manager."""
    # Extract character_id from path or body
    character_id = body.get('character_id', '').strip()

    # Try to extract from path (e.g., /api/characters/huanhuan/open-folder)
    if not character_id:
        parts = parsed.path.strip('/').split('/')
        if len(parts) >= 3 and parts[0] == 'api' and parts[1] == 'characters':
            character_id = parts[2]

    if not character_id:
        return bad(handler, 'character_id is required')

    # Verify the character exists
    chars_dir = _get_characters_dir()
    char_dir = chars_dir / character_id
    if not char_dir.exists():
        return bad(handler, f'Character "{character_id}" not found', 404)

    # Open the character directory using the system file manager
    try:
        system = platform.system()
        if system == 'Darwin':  # macOS
            subprocess.Popen(['open', str(char_dir)])
        elif system == 'Windows':
            subprocess.Popen(['explorer', str(char_dir)])
        elif system == 'Linux':
            subprocess.Popen(['xdg-open', str(char_dir)])
        else:
            return bad(handler, f'Unsupported platform: {system}', 500)

        return j(handler, {
            'ok': True,
            'character_id': character_id,
            'path': str(char_dir),
        })
    except Exception as e:
        return bad(handler, f'Failed to open folder: {_sanitize_error(e)}', 500)


def _handle_frames(handler, parsed):
    """GET /api/frames/{character_id}/frame-{frame_num}.png - Serve animation frame images."""
    # Parse path: /api/frames/{character_id}/frame-{frame_num}.png
    parts = parsed.path.strip('/').split('/')
    if len(parts) < 4 or parts[0] != 'api' or parts[1] != 'frames':
        return bad(handler, 'Invalid frame path', 400)

    character_id = parts[2]
    frame_file = '/'.join(parts[3:])  # Get the remaining parts as filename

    # Validate character_id (alphanumeric, dash, underscore only)
    if not character_id or not _re.match(r'^[a-zA-Z0-9_-]+$', character_id):
        return bad(handler, 'Invalid character_id', 400)

    # Validate frame filename (frame-XXXX.png format)
    if not _re.match(r'^frame-\d{4}\.png$', frame_file):
        return bad(handler, 'Invalid frame filename', 400)

    # Resolve frame file path safely
    chars_dir = _get_characters_dir()
    frame_path = (chars_dir / character_id / 'frames' / frame_file).resolve()

    # Security: ensure resolved path stays within character directory
    try:
        (chars_dir / character_id).resolve().relative_to(chars_dir)
        frame_path.relative_to((chars_dir / character_id).resolve())
    except ValueError:
        return bad(handler, 'Invalid path traversal', 400)

    # Check if file exists
    if not frame_path.exists() or not frame_path.is_file():
        return bad(handler, f'Frame not found: {frame_file}', 404)

    # Serve the image file
    try:
        raw_bytes = frame_path.read_bytes()
        handler.send_response(200)
        handler.send_header('Content-Type', 'image/png')
        handler.send_header('Content-Length', str(len(raw_bytes)))
        handler.send_header('Cache-Control', 'public, max-age=31536000')  # Cache for 1 year
        handler.send_header('Access-Control-Allow-Origin', '*')
        handler.end_headers()
        handler.wfile.write(raw_bytes)
        return True
    except Exception as e:
        return bad(handler, _sanitize_error(e), 500)
