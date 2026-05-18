"""
Hermes Web UI -- SSE streaming engine and agent thread runner.
Includes Sprint 10 cancel support via CANCEL_FLAGS.
"""
import json
import os
import queue
import threading
import time
import traceback
from pathlib import Path

from api.config import (
    IMAGE_EXTS,
    STREAMS, STREAMS_LOCK, CANCEL_FLAGS, CLI_TOOLSETS,
    LOCK, SESSIONS, SESSION_DIR,
    _get_session_agent_lock, _set_thread_env, _clear_thread_env,
    resolve_model_provider,
)

# Global lock for os.environ writes. Per-session locks (_agent_lock) prevent
# concurrent runs of the SAME session, but two DIFFERENT sessions can still
# interleave their os.environ writes. This global lock serializes the env
# save/restore around the entire agent run.
_ENV_LOCK = threading.Lock()


# Free vision models to try in order (first working one wins, used as fallback)
_VISION_FREE_MODELS = [
    "nvidia/nemotron-nano-12b-v2-vl:free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
]

_VISION_PROMPT = (
    "请用中文详细描述这张图片的内容，包括文字、代码、数据、物体、人物、布局、颜色等所有可见信息。"
    " If the image is in English or code, also describe it fully."
)


def _get_minimax_vlm_key() -> tuple[str, str]:
    """Return (api_key, api_host) for MiniMax VLM calls.

    Priority:
      1. ~/Library/Application Support/huanhuan/user/config.json  (set by setup wizard)
      2. HERMES_WEBUI_USER_DIR env var pointing to a config.json

    Returns (api_key, api_host) tuple; empty strings if not found.
    """
    import platform
    candidates = []

    # 1. macOS app user dir (primary — written by the setup wizard)
    if platform.system() == "Darwin":
        candidates.append(
            Path.home() / "Library" / "Application Support" / "huanhuan" / "user" / "config.json"
        )

    # 2. HERMES_WEBUI_USER_DIR override (for dev mode)
    webui_user_dir = os.environ.get("HERMES_WEBUI_USER_DIR", "")
    if webui_user_dir:
        candidates.append(Path(webui_user_dir) / "config.json")

    for cfg_path in candidates:
        try:
            if not cfg_path.exists():
                continue
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            key = cfg.get("llm", {}).get("api_key", "")
            host = cfg.get("llm", {}).get("base_url", "https://api.minimaxi.com/v1")
            # Strip /v1 suffix — we build the full path in _analyze_image_minimax
            host = host.rstrip("/")
            if host.endswith("/v1"):
                host = host[:-3]
            if key and key.startswith("sk-"):
                return key, host
        except Exception:
            continue

    return "", ""


def _analyze_image_minimax(image_path: Path, api_key: str,
                           api_host: str = "https://api.minimaxi.com") -> str:
    """Use MiniMax /v1/coding_plan/vlm endpoint for vision analysis.

    This is the same endpoint used by the MiniMax Coding Plan MCP plugin
    and ClawX. Accepts base64 image_url directly.
    """
    import base64
    import urllib.request

    mime_map = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
                '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp'}
    mime = mime_map.get(image_path.suffix.lower(), 'image/png')

    data = image_path.read_bytes()
    b64 = base64.b64encode(data).decode('ascii')
    data_url = f"data:{mime};base64,{b64}"

    payload = json.dumps({
        "prompt": _VISION_PROMPT,
        "image_url": data_url,
    }).encode('utf-8')
    url = api_host.rstrip("/") + "/v1/coding_plan/vlm"
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "MM-API-Source": "Minimax-MCP",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode('utf-8'))

    # Check API-level error
    base_resp = result.get("base_resp", {})
    if base_resp and base_resp.get("status_code") != 0:
        raise RuntimeError(f"MiniMax VLM error {base_resp.get('status_code')}: {base_resp.get('status_msg')}")

    # Extract content field
    text = result.get("content") or result.get("result") or result.get("text") or ""
    if not text:
        choices = result.get("choices", [])
        if choices:
            text = choices[0].get("message", {}).get("content", "")
    if text:
        return str(text)
    raise RuntimeError(f"MiniMax VLM returned unexpected format: {list(result.keys())}")


def _analyze_image_openrouter(image_path: Path, api_key: str) -> str:
    """Direct OpenRouter vision call, tries free models in order until one works.

    Used as fallback when MiniMax VLM is unavailable.
    """
    import base64
    import urllib.request
    import urllib.error

    mime_map = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
                '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp'}
    mime = mime_map.get(image_path.suffix.lower(), 'image/jpeg')

    data = image_path.read_bytes()
    b64 = base64.b64encode(data).decode('ascii')
    data_url = f"data:{mime};base64,{b64}"

    last_err = None
    for model in _VISION_FREE_MODELS:
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": _VISION_PROMPT},
            ]}],
            "max_tokens": 1024,
        }).encode('utf-8')
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://hermes-agent.nousresearch.com",
                "X-Title": "huanhuan-desktop",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode('utf-8'))
            content = result["choices"][0]["message"]["content"]
            if content:  # Skip models that return null content
                print(f"[webui] vision: used model {model}", flush=True)
                return content
            print(f"[webui] vision: {model} returned empty content, trying next", flush=True)
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code} from {model}"
            print(f"[webui] vision: {model} failed ({e.code}), trying next...", flush=True)
        except Exception as e:
            last_err = str(e)
            print(f"[webui] vision: {model} error ({e}), trying next...", flush=True)

    raise RuntimeError(f"All vision models failed. Last error: {last_err}")


def _preprocess_attachments(attachments, workspace):
    """Analyze attached images and return enriched context text.

    Priority:
      1. MiniMax VLM endpoint (/v1/coding_plan/vlm) — uses the minimax-cn key
         from the active profile's auth.json; no rate limit issues.
      2. OpenRouter free models — fallback if MiniMax key is unavailable.
      3. Hermes vision_analyze_tool — last resort.
    """
    if not attachments:
        return ""

    # 1. Try MiniMax VLM key (primary, no rate limits)
    mm_api_key, mm_api_host = _get_minimax_vlm_key()
    if mm_api_key:
        print("[webui] vision: MiniMax VLM key found, will use as primary", flush=True)
    else:
        print("[webui] vision: no MiniMax key, checking OpenRouter...", flush=True)

    # 2. OpenRouter as fallback
    or_api_key = ""
    if not mm_api_key:
        try:
            from hermes_cli.config import read_raw_config
            _cfg = read_raw_config()
            _aux_vis = _cfg.get("auxiliary", {}).get("vision", {})
            if _aux_vis.get("provider") == "openrouter" and _aux_vis.get("api_key"):
                or_api_key = _aux_vis["api_key"]
        except Exception:
            pass
        if not or_api_key:
            or_api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not or_api_key:
            print("[webui] vision: no OpenRouter key either, vision unavailable", flush=True)

    ws = Path(workspace)
    enriched_parts = []
    for img_path in attachments:
        p = Path(img_path)
        if not p.is_absolute():
            p = ws / p
        if not p.exists() or p.suffix.lower() not in IMAGE_EXTS:
            continue
        size_kb = p.stat().st_size // 1024
        print(f"[webui] vision: analyzing {p.name} ({size_kb}KB)...", flush=True)

        description = None

        # Try MiniMax VLM first
        if mm_api_key:
            try:
                description = _analyze_image_minimax(p, mm_api_key, mm_api_host)
                print(f"[webui] vision: {p.name} analyzed via MiniMax VLM", flush=True)
            except Exception as e:
                print(f"[webui] vision: MiniMax VLM failed ({e}), trying OpenRouter...", flush=True)

        # Try OpenRouter as fallback
        if description is None and or_api_key:
            try:
                description = _analyze_image_openrouter(p, or_api_key)
                print(f"[webui] vision: {p.name} analyzed via OpenRouter", flush=True)
            except Exception as e:
                print(f"[webui] vision: OpenRouter failed ({e}), trying auxiliary_client...", flush=True)

        if description is not None:
            enriched_parts.append(
                f"[The user attached an image. Here's what it contains:\n{description}]"
            )
        else:
            # Last resort: Hermes auxiliary vision tool
            try:
                import asyncio as _asyncio
                from tools.vision_tools import vision_analyze_tool as _vat
                result_json = _asyncio.run(_vat(image_url=str(p), user_prompt=(
                    "Describe everything visible in this image in thorough detail."
                )))
                result = json.loads(result_json)
                if result.get("success"):
                    enriched_parts.append(
                        f"[The user attached an image. Here's what it contains:\n{result.get('analysis', '')}]"
                    )
                else:
                    enriched_parts.append(
                        f"[The user attached an image ({p.name}) but analysis failed. "
                        f"Try: vision_analyze with image_url: {p}]"
                    )
            except Exception as e2:
                enriched_parts.append(
                    f"[The user attached an image ({p.name}). "
                    f"You can examine it with vision_analyze using image_url: {p}]"
                )
                print(f"[webui] vision: all vision methods failed: {e2}", flush=True)

    if not enriched_parts:
        return ""
    return "\n\n".join(enriched_parts) + "\n\n"


# Lazy import to avoid circular deps -- hermes-agent is on sys.path via api/config.py
try:
    from run_agent import AIAgent
except ImportError:
    AIAgent = None
from api.models import get_session, title_from
from api.workspace import set_last_workspace


# ── MiniMax 生图工具（已改为 skill 方式，此处保留备用不再调用） ───────────────
def _register_minimax_image_generate():
    """把 image_generate 工具替换成 MiniMax /v1/image_generation 实现。

    在首次创建 AIAgent 之前调用一次即可。
    """
    try:
        from tools.registry import registry, tool_error
    except ImportError:
        return  # hermes-agent 不在 path，跳过

    def _minimax_generate(args, **_kw):
        import base64, datetime, subprocess, urllib.request as _req
        prompt = args.get('prompt', '').strip()
        if not prompt:
            return tool_error('prompt 不能为空')

        aspect_map = {'landscape': '16:9', 'portrait': '9:16',
                      'square': '1:1', '16:9': '16:9',
                      '1:1': '1:1', '4:3': '4:3', '3:2': '3:2'}
        aspect = aspect_map.get(args.get('aspect_ratio', 'square'), '1:1')

        # 读取 key
        key, host = _get_minimax_vlm_key()
        if not key:
            return tool_error('未找到 MiniMax API Key，请在设置里保存一次')

        import json as _json
        payload = _json.dumps({
            'model': 'image-01',
            'prompt': prompt,
            'response_format': 'base64',
            'n': 1,
            'aspect_ratio': aspect,
        }).encode()
        url = host.rstrip('/') + '/v1/image_generation'
        request = _req.Request(
            url, data=payload,
            headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            with _req.urlopen(request, timeout=120) as resp:
                data = _json.loads(resp.read().decode())
        except Exception as e:
            return tool_error(f'MiniMax 生图请求失败: {e}')

        base_resp = data.get('base_resp', {})
        if base_resp.get('status_code', 0) != 0:
            return tool_error(f"MiniMax 生图失败: {base_resp.get('status_msg')}")

        imgs = data.get('data', {}).get('image_base64', [])
        if not imgs:
            return tool_error('MiniMax 没有返回图片数据')

        # 保存到 Downloads
        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        save_path = Path.home() / 'Downloads' / f'huanhuan_{ts}.png'
        save_path.write_bytes(base64.b64decode(imgs[0]))

        # macOS 直接打开预览
        try:
            subprocess.Popen(['open', str(save_path)])
        except Exception:
            pass

        return _json.dumps({
            'success': True,
            'file': str(save_path),
            'message': f'图片已生成并保存到 {save_path}，同时已用预览打开。',
        }, ensure_ascii=False)

    _schema = {
        'name': 'image_generate',
        'description': (
            '使用 MiniMax image-01 模型根据提示词生成图片。'
            '生成后自动保存到 ~/Downloads/ 并打开预览。'
            '生成完成后告诉用户图片已保存的路径。'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'prompt': {
                    'type': 'string',
                    'description': '详细的图片描述提示词，支持中文和英文。',
                },
                'aspect_ratio': {
                    'type': 'string',
                    'enum': ['square', 'landscape', 'portrait', '16:9', '4:3'],
                    'description': '图片比例：square(1:1) / landscape(16:9) / portrait(9:16)，默认 square。',
                    'default': 'square',
                },
            },
            'required': ['prompt'],
        },
    }
    registry.register(
        name='image_generate',
        toolset='cli',
        schema=_schema,
        handler=_minimax_generate,
        check_fn=None,
        requires_env=[],
        is_async=False,
        description=_schema['description'],
        emoji='🎨',
    )
    print('[webui] image_generate → MiniMax image-01 已注册', flush=True)


# 不在模块加载时注册——要在 AIAgent 创建之后再覆盖，
# 否则 image_generation_tool.py 的懒加载会把我们的注册覆盖掉。
# 实际调用点：_run_agent_streaming() 创建 AIAgent 之后。

# Fields that are safe to send to LLM provider APIs.
# Everything else (attachments, timestamp, _ts, etc.) is display-only
# metadata added by the webui and must be stripped before the API call.
_API_SAFE_MSG_KEYS = {'role', 'content', 'tool_calls', 'tool_call_id', 'name', 'refusal'}


def _sanitize_messages_for_api(messages):
    """Return a deep copy of messages with only API-safe fields.

    The webui stores extra metadata on messages (attachments, timestamp, _ts)
    for display purposes. Some providers (e.g. Z.AI/GLM) reject unknown fields
    instead of ignoring them, causing HTTP 400 errors on subsequent messages.
    """
    clean = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        sanitized = {k: v for k, v in msg.items() if k in _API_SAFE_MSG_KEYS}
        if sanitized.get('role'):
            clean.append(sanitized)
    return clean


def _sse(handler, event, data):
    """Write one SSE event to the response stream."""
    payload = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    handler.wfile.write(payload.encode('utf-8'))
    handler.wfile.flush()


def _run_agent_streaming(session_id, msg_text, model, workspace, stream_id, attachments=None):
    """Run agent in background thread, writing SSE events to STREAMS[stream_id]."""
    q = STREAMS.get(stream_id)
    if q is None:
        return

    # Sprint 10: create a cancel event for this stream
    cancel_event = threading.Event()
    with STREAMS_LOCK:
        CANCEL_FLAGS[stream_id] = cancel_event

    def put(event, data):
        # If cancelled, drop all further events except the cancel event itself
        if cancel_event.is_set() and event not in ('cancel', 'error'):
            return
        try:
            q.put_nowait((event, data))
            print(f"[webui] SSE event queued: {event} (stream_id={stream_id})", flush=True)
        except Exception as e:
            print(f"[webui] ERROR: Failed to queue SSE event {event}: {e}", flush=True)

    try:
        s = get_session(session_id)
        s.workspace = str(Path(workspace).expanduser().resolve())
        s.model = model

        _agent_lock = _get_session_agent_lock(session_id)
        # TD1: set thread-local env context so concurrent sessions don't clobber globals
        # Check for pre-flight cancel (user cancelled before agent even started)
        if cancel_event.is_set():
            put('cancel', {'message': 'Cancelled before start'})
            return

        # Resolve profile home for this agent run (snapshot at start)
        try:
            from api.profiles import get_active_hermes_home
            _profile_home = str(get_active_hermes_home())
        except ImportError:
            _profile_home = os.environ.get('HERMES_HOME', '')

        _set_thread_env(
            TERMINAL_CWD=str(s.workspace),
            HERMES_EXEC_ASK='1',
            HERMES_SESSION_KEY=session_id,
            HERMES_HOME=_profile_home,
        )
        # Still set process-level env as fallback for tools that bypass thread-local
        # Acquire lock only for the env mutation, then release before the agent runs.
        # The finally block re-acquires to restore — keeping critical sections short
        # and preventing a deadlock where the restore would re-enter the same lock.
        with _ENV_LOCK:
          old_cwd = os.environ.get('TERMINAL_CWD')
          old_exec_ask = os.environ.get('HERMES_EXEC_ASK')
          old_session_key = os.environ.get('HERMES_SESSION_KEY')
          old_hermes_home = os.environ.get('HERMES_HOME')
          os.environ['TERMINAL_CWD'] = str(s.workspace)
          os.environ['HERMES_EXEC_ASK'] = '1'
          os.environ['HERMES_SESSION_KEY'] = session_id
          if _profile_home:
              os.environ['HERMES_HOME'] = _profile_home
        # Lock released — agent runs without holding it
        # Register a gateway-style notify callback so the approval system can
        # push the `approval` SSE event the moment a dangerous command is
        # detected, without waiting for the next on_tool() poll cycle.
        # Without this, the agent thread blocks inside the terminal tool
        # waiting for approval that the UI never knew to ask for, leaving
        # the chat stuck in "Thinking…" forever.
        _approval_registered = False
        _unreg_notify = None
        try:
            from tools.approval import (
                register_gateway_notify as _reg_notify,
                unregister_gateway_notify as _unreg_notify,
            )
            def _approval_notify_cb(approval_data):
                put('approval', approval_data)
            _reg_notify(session_id, _approval_notify_cb)
            _approval_registered = True
        except ImportError:
            pass  # approval module not available — fall back to polling

        try:
            def on_token(text):
                if text is None:
                    return  # end-of-stream sentinel
                put('token', {'text': text})

            def on_tool(name, preview, args):
                args_snap = {}
                if isinstance(args, dict):
                    for k, v in list(args.items())[:4]:
                        s2 = str(v); args_snap[k] = s2[:120]+('...' if len(s2)>120 else '')
                put('tool', {'name': name, 'preview': preview, 'args': args_snap})
                # Fallback: poll for pending approval in case notify_cb wasn't
                # registered (e.g. older approval module without gateway support).
                try:
                    from tools.approval import has_pending as _has_pending, _pending, _lock
                    if _has_pending(session_id):
                        with _lock:
                            p = dict(_pending.get(session_id, {}))
                        if p:
                            put('approval', p)
                except ImportError:
                    pass

            if AIAgent is None:
                raise ImportError("AIAgent not available -- check that hermes-agent is on sys.path")
            resolved_model, resolved_provider, resolved_base_url = resolve_model_provider(model)

            # Resolve API key via Hermes runtime provider (matches gateway behaviour).
            # Pass the resolved provider so non-default providers get their own credentials.
            resolved_api_key = None
            try:
                from hermes_cli.runtime_provider import resolve_runtime_provider
                _rt = resolve_runtime_provider(requested=resolved_provider)
                resolved_api_key = _rt.get("api_key")
                if not resolved_provider:
                    resolved_provider = _rt.get("provider")
                if not resolved_base_url:
                    resolved_base_url = _rt.get("base_url")
            except Exception as _e:
                print(f"[webui] WARNING: resolve_runtime_provider failed: {_e}", flush=True)

            # Read per-profile config at call time (not module-level snapshot)
            from api.config import get_config as _get_config
            _cfg = _get_config()

            # Per-profile toolsets (fall back to module-level CLI_TOOLSETS)
            _pt = _cfg.get('platform_toolsets', {})
            _toolsets = _pt.get('cli', CLI_TOOLSETS) if isinstance(_pt, dict) else CLI_TOOLSETS

            # Fallback model from profile config (e.g. for rate-limit recovery)
            _fallback = _cfg.get('fallback_model') or None
            if _fallback:
                # Resolve the fallback through our provider logic too
                fb_model = _fallback.get('model', '')
                fb_provider = _fallback.get('provider', '')
                fb_base_url = _fallback.get('base_url')
                _fallback_resolved = {
                    'model': fb_model,
                    'provider': fb_provider,
                    'base_url': fb_base_url,
                }
            else:
                _fallback_resolved = None

            agent = AIAgent(
                model=resolved_model,
                provider=resolved_provider,
                base_url=resolved_base_url,
                api_key=resolved_api_key,
                platform='cli',
                quiet_mode=True,
                enabled_toolsets=_toolsets,
                fallback_model=_fallback_resolved,
                session_id=session_id,
                stream_delta_callback=on_token,
                tool_progress_callback=on_tool,
            )
            # Prepend workspace context so the agent always knows which directory
            # to use for file operations, regardless of session age or AGENTS.md defaults.
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
            # Resolve personality prompt from config.yaml agent.personalities
            # (matches hermes-agent CLI behavior — passes via ephemeral_system_prompt)
            _personality_prompt = None
            _pname = getattr(s, 'personality', None)
            if _pname:
                _agent_cfg = _cfg.get('agent', {})
                _personalities = _agent_cfg.get('personalities', {})
                if isinstance(_personalities, dict) and _pname in _personalities:
                    _pval = _personalities[_pname]
                    if isinstance(_pval, dict):
                        _parts = [_pval.get('system_prompt', '') or _pval.get('prompt', '')]
                        if _pval.get('tone'):
                            _parts.append(f'Tone: {_pval["tone"]}')
                        if _pval.get('style'):
                            _parts.append(f'Style: {_pval["style"]}')
                        _personality_prompt = '\n'.join(p for p in _parts if p)
                    else:
                        _personality_prompt = str(_pval)
            # Pass personality via ephemeral_system_prompt (agent's own mechanism)
            if _personality_prompt:
                agent.ephemeral_system_prompt = _personality_prompt

            # Pre-process image attachments through vision tool before sending to agent
            vision_enriched = _preprocess_attachments(attachments, workspace)

            result = agent.run_conversation(
                user_message=workspace_ctx + vision_enriched + msg_text,
                system_message=workspace_system_msg,
                conversation_history=_sanitize_messages_for_api(s.messages),
                task_id=session_id,
                persist_user_message=msg_text,
            )
            s.messages = result.get('messages') or s.messages

            # ── Handle context compression side effects ──
            # If compression fired inside run_conversation, the agent may have
            # rotated its session_id. Detect and fix the mismatch so the WebUI
            # continues writing to the correct session file.
            _agent_sid = getattr(agent, 'session_id', None)
            _compressed = False
            if _agent_sid and _agent_sid != session_id:
                old_sid = session_id
                new_sid = _agent_sid
                # Rename the session file
                old_path = SESSION_DIR / f'{old_sid}.json'
                new_path = SESSION_DIR / f'{new_sid}.json'
                s.session_id = new_sid
                with LOCK:
                    if old_sid in SESSIONS:
                        SESSIONS[new_sid] = SESSIONS.pop(old_sid)
                if old_path.exists() and not new_path.exists():
                    try:
                        old_path.rename(new_path)
                    except OSError:
                        pass
                _compressed = True
            # Also detect compression via the result dict or compressor state
            if not _compressed:
                _compressor = getattr(agent, 'context_compressor', None)
                if _compressor and getattr(_compressor, 'compression_count', 0) > 0:
                    _compressed = True
            # Notify the frontend that compression happened
            if _compressed:
                put('compressed', {
                    'message': 'Context auto-compressed to continue the conversation',
                })

            # Stamp 'timestamp' on any messages that don't have one yet
            _now = time.time()
            for _m in s.messages:
                if isinstance(_m, dict) and not _m.get('timestamp') and not _m.get('_ts'):
                    _m['timestamp'] = int(_now)
            s.title = title_from(s.messages, s.title)
            # Read token/cost usage from the agent object (if available)
            input_tokens = getattr(agent, 'session_prompt_tokens', 0) or 0
            output_tokens = getattr(agent, 'session_completion_tokens', 0) or 0
            estimated_cost = getattr(agent, 'session_estimated_cost_usd', None)
            s.input_tokens = (s.input_tokens or 0) + input_tokens
            s.output_tokens = (s.output_tokens or 0) + output_tokens
            if estimated_cost:
                s.estimated_cost = (s.estimated_cost or 0) + estimated_cost
            # Extract tool call metadata grouped by assistant message index
            # Each tool call gets assistant_msg_idx so the client can render
            # cards inline with the assistant bubble that triggered them.
            tool_calls = []
            pending_names = {}   # tool_call_id -> name
            pending_args = {}    # tool_call_id -> args dict
            pending_asst_idx = {} # tool_call_id -> index in s.messages
            for msg_idx, m in enumerate(s.messages):
                if m.get('role') == 'assistant':
                    c = m.get('content', '')
                    # Anthropic format: content is a list with type=tool_use blocks
                    if isinstance(c, list):
                        for p in c:
                            if isinstance(p, dict) and p.get('type') == 'tool_use':
                                tid = p.get('id', '')
                                pending_names[tid] = p.get('name', '')
                                pending_args[tid] = p.get('input', {})
                                pending_asst_idx[tid] = msg_idx
                    # OpenAI format: tool_calls as top-level field on the message
                    for tc in m.get('tool_calls', []):
                        if not isinstance(tc, dict):
                            continue
                        tid = tc.get('id', '') or tc.get('call_id', '')
                        fn = tc.get('function', {})
                        name = fn.get('name', '')
                        try:
                            import json as _j
                            args = _j.loads(fn.get('arguments', '{}') or '{}')
                        except Exception:
                            args = {}
                        if tid and name:
                            pending_names[tid] = name
                            pending_args[tid] = args
                            pending_asst_idx[tid] = msg_idx
                elif m.get('role') == 'tool':
                    tid = m.get('tool_call_id') or m.get('tool_use_id', '')
                    name = pending_names.get(tid, '')
                    if not name or name == 'tool':
                        continue  # skip unresolvable tool entries
                    asst_idx = pending_asst_idx.get(tid, -1)
                    args = pending_args.get(tid, {})
                    raw = str(m.get('content', ''))
                    try:
                        rd = json.loads(raw)
                        snippet = str(rd.get('output') or rd.get('result') or rd.get('error') or raw)[:200]
                    except Exception:
                        snippet = raw[:200]
                    # Truncate args values for storage
                    args_snap = {}
                    if isinstance(args, dict):
                        for k, v in list(args.items())[:6]:
                            s2 = str(v)
                            args_snap[k] = s2[:120] + ('...' if len(s2) > 120 else '')
                    tool_calls.append({
                        'name': name, 'snippet': snippet, 'tid': tid,
                        'assistant_msg_idx': asst_idx, 'args': args_snap,
                    })
            s.tool_calls = tool_calls
            # Tag the matching user message with attachment filenames for display on reload
            # Only tag a user message whose content relates to this turn's text
            # (msg_text is the full message including the [Attached files: ...] suffix)
            if attachments:
                for m in reversed(s.messages):
                    if m.get('role') == 'user':
                        content = str(m.get('content', ''))
                        # Match if content is part of the sent message or vice-versa
                        base_text = msg_text.split('\n\n[Attached files:')[0].strip()
                        if base_text[:60] in content or content[:60] in msg_text:
                            m['attachments'] = attachments
                            break
            s.save()
            # Sync to state.db for /insights (opt-in setting)
            try:
                from api.config import load_settings as _load_settings
                if _load_settings().get('sync_to_insights'):
                    from api.state_sync import sync_session_usage
                    sync_session_usage(
                        session_id=s.session_id,
                        input_tokens=s.input_tokens or 0,
                        output_tokens=s.output_tokens or 0,
                        estimated_cost=s.estimated_cost,
                        model=model,
                        title=s.title,
                        message_count=len(s.messages),
                    )
            except Exception:
                pass  # never crash the stream for sync failures
            usage = {'input_tokens': input_tokens, 'output_tokens': output_tokens, 'estimated_cost': estimated_cost}
            # Include context window data from the agent's compressor for the UI indicator
            _cc = getattr(agent, 'context_compressor', None)
            if _cc:
                usage['context_length'] = getattr(_cc, 'context_length', 0) or 0
                usage['threshold_tokens'] = getattr(_cc, 'threshold_tokens', 0) or 0
                usage['last_prompt_tokens'] = getattr(_cc, 'last_prompt_tokens', 0) or 0
            put('done', {'session': s.compact() | {'messages': s.messages, 'tool_calls': tool_calls}, 'usage': usage})
        finally:
            # Unregister the gateway approval callback and unblock any threads
            # still waiting on approval (e.g. stream cancelled mid-approval).
            if _approval_registered and _unreg_notify is not None:
                try:
                    _unreg_notify(session_id)
                except Exception:
                    pass
            with _ENV_LOCK:
                if old_cwd is None: os.environ.pop('TERMINAL_CWD', None)
                else: os.environ['TERMINAL_CWD'] = old_cwd
                if old_exec_ask is None: os.environ.pop('HERMES_EXEC_ASK', None)
                else: os.environ['HERMES_EXEC_ASK'] = old_exec_ask
                if old_session_key is None: os.environ.pop('HERMES_SESSION_KEY', None)
                else: os.environ['HERMES_SESSION_KEY'] = old_session_key
                if old_hermes_home is None: os.environ.pop('HERMES_HOME', None)
                else: os.environ['HERMES_HOME'] = old_hermes_home

    except Exception as e:
        print('[webui] stream error:\n' + traceback.format_exc(), flush=True)
        err_str = str(e)
        # Detect rate limit errors specifically so the client can show a helpful card
        # rather than the generic "Connection lost" message
        is_rate_limit = 'rate limit' in err_str.lower() or '429' in err_str or 'RateLimitError' in type(e).__name__
        if is_rate_limit:
            put('apperror', {
                'message': err_str,
                'type': 'rate_limit',
                'hint': 'Rate limit reached. The fallback model (if configured) was also exhausted. Try again in a moment.',
            })
        else:
            put('apperror', {'message': err_str, 'type': 'error'})
    finally:
        _clear_thread_env()  # TD1: always clear thread-local context
        with STREAMS_LOCK:
            STREAMS.pop(stream_id, None)
            CANCEL_FLAGS.pop(stream_id, None)

# ============================================================
# SECTION: HTTP Request Handler
# do_GET: read-only API endpoints + SSE stream + static HTML
# do_POST: mutating endpoints (session CRUD, chat, upload, approval)
# Routing is a flat if/elif chain. See ARCHITECTURE.md section 4.1.
# ============================================================


def cancel_stream(stream_id: str) -> bool:
    """Signal an in-flight stream to cancel. Returns True if the stream existed."""
    with STREAMS_LOCK:
        if stream_id not in STREAMS:
            return False
        flag = CANCEL_FLAGS.get(stream_id)
        if flag:
            flag.set()
        # Put a cancel sentinel into the queue so the SSE handler wakes up
        q = STREAMS.get(stream_id)
        if q:
            try:
                q.put_nowait(('cancel', {'message': 'Cancelled by user'}))
            except Exception:
                pass
    return True
