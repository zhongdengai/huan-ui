/**
 * llm-config.js — AI 配置面板逻辑
 * 管理 LLM provider 选择、API Key 填写、测试连接、保存配置
 */

// ── Provider 元数据（与后端 PROVIDERS 同步） ─────────────────────────────────
const LLM_PROVIDERS = {
  minimax:  { name: 'MiniMax',      emoji: '🟣', requiresKey: true,  defaultModel: 'MiniMax-Text-01',      defaultUrl: 'https://api.minimax.io/v1' },
  claude:   { name: 'Claude',       emoji: '🟠', requiresKey: true,  defaultModel: 'claude-haiku-3-5',     defaultUrl: 'https://api.anthropic.com' },
  deepseek: { name: 'DeepSeek',     emoji: '🔵', requiresKey: true,  defaultModel: 'deepseek-chat',        defaultUrl: 'https://api.deepseek.com/v1' },
  openai:   { name: 'OpenAI',       emoji: '🟢', requiresKey: true,  defaultModel: 'gpt-4o-mini',          defaultUrl: 'https://api.openai.com/v1' },
  qwen:     { name: 'Qwen 通义',    emoji: '🟡', requiresKey: true,  defaultModel: 'qwen-plus',            defaultUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1' },
  gemini:   { name: 'Gemini',       emoji: '🔴', requiresKey: true,  defaultModel: 'gemini-1.5-flash',     defaultUrl: 'https://generativelanguage.googleapis.com/v1beta' },
  ollama:   { name: 'Ollama 本地',  emoji: '⚫', requiresKey: false, defaultModel: 'llama3',               defaultUrl: 'http://localhost:11434/v1' },
  custom:   { name: '自定义',        emoji: '⚙️', requiresKey: false, defaultModel: '',                     defaultUrl: '' },
};

let _llmSelectedProvider = null;  // 当前选中的 provider ID
let _llmSavedConfig = {};          // 已保存的配置（从服务器读取）
let _llmKeyMasked = '';            // 已保存的 key 掩码（不含完整 key）

// ── 初始化面板 ────────────────────────────────────────────────────────────────
async function llmConfigInit() {
  llmRenderGrid();
  await llmLoadConfig();
}

// 渲染 8 个 provider 卡片
function llmRenderGrid() {
  const grid = document.getElementById('llmProviderGrid');
  if (!grid) return;
  grid.innerHTML = '';
  Object.entries(LLM_PROVIDERS).forEach(([id, p]) => {
    const card = document.createElement('button');
    card.id = `llmCard_${id}`;
    card.style.cssText = `
      background: rgba(255,255,255,.05);
      border: 1px solid var(--border2);
      border-radius: 10px;
      padding: 10px 8px 8px;
      cursor: pointer;
      text-align: center;
      color: var(--text);
      font-family: inherit;
      transition: background .15s, border-color .15s;
    `;
    card.innerHTML = `
      <div style="font-size:20px;margin-bottom:4px">${p.emoji}</div>
      <div style="font-size:11px;font-weight:600;line-height:1.2">${p.name}</div>
      <div id="llmCardBadge_${id}" style="font-size:9px;color:var(--muted);margin-top:3px;height:12px"></div>
    `;
    card.addEventListener('click', () => llmSelectProvider(id));
    card.addEventListener('mouseenter', () => {
      if (_llmSelectedProvider !== id)
        card.style.background = 'rgba(255,255,255,.1)';
    });
    card.addEventListener('mouseleave', () => {
      if (_llmSelectedProvider !== id)
        card.style.background = 'rgba(255,255,255,.05)';
    });
    grid.appendChild(card);
  });
}

// 从服务器加载已保存的配置
async function llmLoadConfig() {
  try {
    const res = await fetch('/api/llm-config');
    const data = await res.json();
    _llmSavedConfig = data.llm || {};
    _llmKeyMasked = _llmSavedConfig.api_key_masked || '';
    llmUpdateStatusBar();
    llmUpdateCardBadges();
    // 如果有已保存的配置，自动展开对应的 provider
    if (_llmSavedConfig.provider) {
      llmSelectProvider(_llmSavedConfig.provider, /* autoOpen */ true);
    }
  } catch (e) {
    console.error('[llm-config] load failed:', e);
    const bar = document.getElementById('llmCurrentStatus');
    if (bar) bar.textContent = '⚠ 加载配置失败';
  }
}

// 更新顶部状态条
function llmUpdateStatusBar() {
  const bar = document.getElementById('llmCurrentStatus');
  if (!bar) return;
  const p = _llmSavedConfig.provider;
  if (p && LLM_PROVIDERS[p]) {
    const info = LLM_PROVIDERS[p];
    const model = _llmSavedConfig.model_id || info.defaultModel;
    const keyOk = !info.requiresKey || _llmKeyMasked;
    bar.innerHTML = keyOk
      ? `✅ 当前使用 <strong>${info.name}</strong> &nbsp;·&nbsp; ${model}`
      : `⚠ 已选择 <strong>${info.name}</strong> 但未配置 Key`;
    bar.style.color = keyOk ? '#6bcf7f' : '#f4a261';
  } else {
    bar.textContent = '⚙ 未配置 — 请选择一个 LLM 提供商';
    bar.style.color = 'var(--muted)';
  }
}

// 在对应卡片上显示"已配置"标记
function llmUpdateCardBadges() {
  Object.keys(LLM_PROVIDERS).forEach(id => {
    const badge = document.getElementById(`llmCardBadge_${id}`);
    if (!badge) return;
    if (id === _llmSavedConfig.provider) {
      badge.textContent = '✓ 已配置';
      badge.style.color = '#6bcf7f';
    } else {
      badge.textContent = '';
    }
  });
}

// 选中某个 provider，展开表单
function llmSelectProvider(providerId, autoOpen = false) {
  _llmSelectedProvider = providerId;
  const pInfo = LLM_PROVIDERS[providerId];
  if (!pInfo) return;

  // 高亮选中的卡片
  Object.keys(LLM_PROVIDERS).forEach(id => {
    const card = document.getElementById(`llmCard_${id}`);
    if (!card) return;
    if (id === providerId) {
      card.style.background = 'rgba(99,179,237,.2)';
      card.style.borderColor = '#63b3ed';
    } else {
      card.style.background = 'rgba(255,255,255,.05)';
      card.style.borderColor = 'var(--border2)';
    }
  });

  // 填充表单
  const nameEl = document.getElementById('llmFormProviderName');
  if (nameEl) nameEl.textContent = pInfo.name;

  // API Key 行
  const keyRow = document.getElementById('llmKeyRow');
  const keyInput = document.getElementById('llmApiKey');
  const keyMaskedEl = document.getElementById('llmKeyMasked');
  if (keyRow) keyRow.style.display = pInfo.requiresKey ? 'block' : 'none';
  if (keyInput) {
    keyInput.value = '';
    keyInput.placeholder = pInfo.requiresKey ? '粘贴 API Key...' : '（无需 Key）';
  }
  // 显示掩码（如果有已保存的 key）
  if (keyMaskedEl) {
    if (providerId === _llmSavedConfig.provider && _llmKeyMasked) {
      keyMaskedEl.textContent = `已保存：${_llmKeyMasked}（留空保持不变）`;
      keyMaskedEl.style.display = 'block';
    } else {
      keyMaskedEl.style.display = 'none';
    }
  }

  // Base URL
  const urlInput = document.getElementById('llmBaseUrl');
  if (urlInput) {
    if (providerId === _llmSavedConfig.provider && _llmSavedConfig.base_url) {
      urlInput.value = _llmSavedConfig.base_url;
    } else {
      urlInput.value = '';
    }
    urlInput.placeholder = pInfo.defaultUrl || '（使用默认）';
  }

  // Model ID
  const modelInput = document.getElementById('llmModelId');
  if (modelInput) {
    if (providerId === _llmSavedConfig.provider && _llmSavedConfig.model_id) {
      modelInput.value = _llmSavedConfig.model_id;
    } else {
      modelInput.value = '';
    }
    modelInput.placeholder = pInfo.defaultModel || '（使用默认）';
  }

  // 清除状态提示
  llmSetFormStatus('', '');

  // 展开表单
  const form = document.getElementById('llmConfigForm');
  if (form) form.style.display = 'block';
}

// ── 构建当前表单的配置对象 ────────────────────────────────────────────────────
function llmBuildConfig(includeSavedKey = true) {
  const keyInput = document.getElementById('llmApiKey');
  const urlInput = document.getElementById('llmBaseUrl');
  const modelInput = document.getElementById('llmModelId');

  let apiKey = (keyInput ? keyInput.value.trim() : '');
  // 如果输入框为空且是同一 provider，保持已保存的 key（通过后端处理）
  // 实际上我们在保存时把 keep_existing_key flag 传给后端
  const keepExistingKey = !apiKey && includeSavedKey
    && _llmSelectedProvider === _llmSavedConfig.provider;

  return {
    provider: _llmSelectedProvider,
    api_key: apiKey,
    base_url: (urlInput ? urlInput.value.trim() : ''),
    model_id: (modelInput ? modelInput.value.trim() : ''),
    _keep_existing_key: keepExistingKey,
  };
}

// ── 测试连接 ──────────────────────────────────────────────────────────────────
async function llmTestConnection() {
  if (!_llmSelectedProvider) return;
  llmSetFormStatus('⏳ 连接测试中...', 'info');

  const cfg = llmBuildConfig(true);
  // 如果 key 为空且不保持原来的，提示填写
  const pInfo = LLM_PROVIDERS[_llmSelectedProvider];
  if (pInfo.requiresKey && !cfg.api_key && !cfg._keep_existing_key) {
    llmSetFormStatus('请先填写 API Key', 'error');
    return;
  }

  try {
    const res = await fetch('/api/llm-config/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ llm: cfg }),
    });
    const data = await res.json();
    if (data.ok) {
      llmSetFormStatus(data.message || '连接成功 ✓', 'success');
    } else {
      llmSetFormStatus(data.message || '连接失败', 'error');
    }
  } catch (e) {
    llmSetFormStatus(`测试失败：${e.message}`, 'error');
  }
}

// ── 保存配置 ──────────────────────────────────────────────────────────────────
async function llmSaveConfig() {
  if (!_llmSelectedProvider) return;
  llmSetFormStatus('💾 保存中...', 'info');

  const cfg = llmBuildConfig(true);
  const pInfo = LLM_PROVIDERS[_llmSelectedProvider];
  if (pInfo.requiresKey && !cfg.api_key && !cfg._keep_existing_key) {
    llmSetFormStatus('请先填写 API Key', 'error');
    return;
  }

  try {
    const res = await fetch('/api/llm-config/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ llm: cfg }),
    });
    const data = await res.json();
    if (data.ok) {
      llmSetFormStatus('✅ 已保存！欢欢下次对话将使用新配置', 'success');
      // 重新加载配置更新状态栏
      await llmLoadConfig();
    } else {
      llmSetFormStatus(`保存失败：${data.error || '未知错误'}`, 'error');
    }
  } catch (e) {
    llmSetFormStatus(`保存失败：${e.message}`, 'error');
  }
}

// ── 设置表单状态提示 ──────────────────────────────────────────────────────────
function llmSetFormStatus(msg, type) {
  const el = document.getElementById('llmFormStatus');
  if (!el) return;
  if (!msg) { el.style.display = 'none'; return; }
  el.textContent = msg;
  el.style.display = 'block';
  const colors = {
    success: { bg: 'rgba(107,207,127,.15)', color: '#6bcf7f' },
    error:   { bg: 'rgba(244,162,97,.15)',  color: '#f4a261' },
    info:    { bg: 'rgba(255,255,255,.08)', color: 'var(--muted)' },
  };
  const c = colors[type] || colors.info;
  el.style.background = c.bg;
  el.style.color = c.color;
}

// ── 面板切换时初始化 ──────────────────────────────────────────────────────────
// 由 panels.js 的 switchPanel 调用
window.onLlmConfigPanelOpen = llmConfigInit;
