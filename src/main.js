// 确保Tauri API已加载，否则等待
let invoke, win, getCurrentWindow, LogicalPosition;

if (window.__TAURI__) {
  invoke = window.__TAURI__.core.invoke;
  getCurrentWindow = window.__TAURI__.window.getCurrentWindow;
  LogicalPosition = window.__TAURI__.window.LogicalPosition;
  win = getCurrentWindow();
} else {
  console.warn('[init] Tauri not ready yet, will initialize on first use');
  // 延迟初始化
  const initTauri = () => {
    invoke = window.__TAURI__.core.invoke;
    getCurrentWindow = window.__TAURI__.window.getCurrentWindow;
    LogicalPosition = window.__TAURI__.window.LogicalPosition;
    win = getCurrentWindow();
  };

  // 如果Tauri还没加载，等待
  if (!window.__TAURI__) {
    document.addEventListener('DOMContentLoaded', () => {
      if (window.__TAURI__) {
        initTauri();
      }
    }, { once: true });
  } else {
    initTauri();
  }
}
const app = document.getElementById('app');
const bubble = document.getElementById('bubble');
const bubbleText = document.getElementById('bubble-text');
const msgInput = document.getElementById('msg-input');
const inputWrap = document.getElementById('input-wrap');
const contextMenu = document.getElementById('context-menu');
const quitBtn = document.getElementById('quit-btn');
const switchCharacterBtn = document.getElementById('switch-character-btn');
const characterSubmenu = document.getElementById('character-submenu');
const avatar = document.getElementById('avatar');
const copyBtn = document.getElementById('copy-btn');
const voiceBtn = document.getElementById('voice-btn');
const voiceInputBtn = document.getElementById('voice-input-btn');
const fileBtn = document.getElementById('file-btn');

// ── Canvas 动画播放 ────────────────────────────────────────────
const canvas = avatar;
const ctx = canvas.getContext('2d', { alpha: true });
const FRAME_RATE = 24; // fps
// 注意：在Tauri中使用127.0.0.1而不是localhost，因为localhost解析可能不同
const BACKEND_URL = typeof window.__TAURI__ !== 'undefined'
  ? 'http://127.0.0.1:8868'  // Tauri环境
  : 'http://localhost:8868';  // 浏览器环境

// 动态变量
let currentCharacter = null;
let totalFrames = 0;
let frames = [];
let currentFrame = 0;
let isPlaying = false;
let animationFrameId = null;

// ── 粒子效果系统 ──
class Particle {
  constructor(x, y) {
    this.x = x;
    this.y = y;
    this.vx = (Math.random() - 0.5) * 2; // 随机水平速度
    this.vy = Math.random() * -2 - 1; // 向上浮动
    this.life = 1.0; // 透明度（1 = 完全不透明，0 = 完全透明）
    this.fadeSpeed = Math.random() * 0.01 + 0.005; // 淡出速度
    this.size = Math.random() * 3 + 1; // 粒子大小
  }

  update() {
    this.x += this.vx;
    this.y += this.vy;
    this.life -= this.fadeSpeed;
  }

  draw(ctx) {
    ctx.save();
    ctx.globalAlpha = this.life * 0.6;
    ctx.fillStyle = '#ffffff'; // 白色粒子
    ctx.beginPath();
    ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  isAlive() {
    return this.life > 0;
  }
}

let particles = [];

function emitParticles(x, y, count = 2) {
  // 在指定位置生成新粒子
  for (let i = 0; i < count; i++) {
    particles.push(new Particle(x + (Math.random() - 0.5) * 20, y + (Math.random() - 0.5) * 20));
  }
}

function updateAndDrawParticles() {
  // 更新并绘制粒子
  particles = particles.filter(p => p.isAlive());

  for (let p of particles) {
    p.update();
    p.draw(ctx);
  }
}

// 初始化 Canvas 尺寸（动态，基于加载的帧）
function initCanvas(frameWidth = null, frameHeight = null) {
  const wrap = document.getElementById('avatar-wrap');

  // 如果提供了帧尺寸，使用帧尺寸；否则使用默认值
  let width = frameWidth || 400;
  let height = frameHeight || 400;

  // 确保最小尺寸
  width = Math.max(width, 200);
  height = Math.max(height, 200);

  canvas.width = width;
  canvas.height = height;
  canvas.style.width = width + 'px';
  canvas.style.height = height + 'px';
  canvas.style.display = 'block';
  canvas.style.background = 'transparent'; // 确保背景透明

  // 清除背景
  ctx.clearRect(0, 0, width, height);

  console.log('[canvas] 初始化完成：', width, 'x', height);
}

// 从服务器加载当前人物的配置
async function initCharacter() {
  try {
    console.log('[character] 正在从服务器加载人物配置...');
    const response = await fetch(`${BACKEND_URL}/api/characters/list`);

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const data = await response.json();
    const activeCharacterId = data.active;
    const characterConfig = data.characters.find(c => c.id === activeCharacterId);

    if (!characterConfig) {
      throw new Error(`找不到人物配置: ${activeCharacterId}`);
    }

    currentCharacter = characterConfig.id;
    totalFrames = characterConfig.frames_count || 0;

    console.log(`[character] ✓ 已加载人物: ${characterConfig.name} (${currentCharacter})`);
    console.log(`[character] ✓ 帧数: ${totalFrames}`);

    return true;
  } catch (err) {
    console.error('[character] 加载配置失败:', err);
    // 降级方案：使用默认配置
    currentCharacter = 'huanhuan';
    totalFrames = 121;
    console.warn('[character] ⚠️  使用默认配置: huanhuan (121帧)');
    return false;
  }
}

// 预加载所有 PNG 帧（参数化版本）
async function loadFrames(characterId = null) {
  const charId = characterId || currentCharacter;

  if (!charId) {
    console.error('[frames] 错误: 没有指定人物ID');
    return false;
  }

  if (totalFrames <= 0) {
    console.warn(`[frames] ⚠️  人物 ${charId} 没有帧数据 (${totalFrames})`);
    return false;
  }

  console.log(`[frames] 开始加载 ${totalFrames} 个 PNG 帧 (${charId})...`);
  frames = [];

  const cachebust = Date.now();  // 缓存破坏者：使用当前时间戳
  for (let i = 1; i <= totalFrames; i++) {
    const frameNum = String(i).padStart(4, '0');
    const img = new Image();
    img.crossOrigin = 'anonymous';
    // 使用参数化的HTTP URL，加上缓存破坏参数
    img.src = `${BACKEND_URL}/api/frames/${charId}/frame-${frameNum}.png?t=${cachebust}`;

    frames.push(
      new Promise((resolve, reject) => {
        img.onload = () => {
          if (i % 20 === 0 || i === 1 || i === totalFrames) {
            console.log(`[frames] 已加载: frame-${frameNum}.png (${i}/${totalFrames})`);
          }
          resolve(img);
        };
        img.onerror = () => {
          console.error(`[frames] 加载失败: frame-${frameNum}.png`);
          reject(new Error(`Failed to load ${charId}/frame-${frameNum}.png`));
        };
      })
    );
  }

  try {
    const loadedFrames = await Promise.all(frames);
    console.log(`[frames] ✅ 成功加载 ${loadedFrames.length} 个帧`);
    frames = loadedFrames;

    // 加载完毕，重新初始化 Canvas 并绘制第一帧
    if (frames.length > 0) {
      const firstFrame = frames[0];
      console.log(`[frames] 第一帧尺寸: ${firstFrame.naturalWidth}x${firstFrame.naturalHeight}`);
      // 使用实际帧尺寸初始化 Canvas
      initCanvas(firstFrame.naturalWidth, firstFrame.naturalHeight);

      currentFrame = 0;
      drawFrame(0);
      console.log('[frames] 已绘制第一帧');
    }

    return true;
  } catch (err) {
    console.error('[frames] 加载失败:', err);
    frames = [];
    return false;
  }
}

// 绘制指定帧（居中显示 + 移除黑色背景）
function drawFrame(frameIndex) {
  if (frameIndex >= frames.length) {
    console.warn('[draw] 帧索引超出范围:', frameIndex);
    return;
  }

  const img = frames[frameIndex];
  const canvasW = canvas.width;
  const canvasH = canvas.height;
  const imgW = img.naturalWidth;
  const imgH = img.naturalHeight;

  // 清除 Canvas
  ctx.clearRect(0, 0, canvasW, canvasH);

  // 计算居中位置
  const x = (canvasW - imgW) / 2;
  const y = (canvasH - imgH) / 2;

  // 先绘制图像
  ctx.drawImage(img, x, y, imgW, imgH);

  // 移除黑色和天青色背景
  const imageData = ctx.getImageData(0, 0, canvasW, canvasH);
  const data = imageData.data;

  // 删除黑色背景和天青色背景
  const BLACK_THRESHOLD = 40;
  const CYAN_R = 135, CYAN_G = 206, CYAN_B = 235;
  const CYAN_TOLERANCE = 30;

  for (let i = 0; i < data.length; i += 4) {
    const r = data[i];
    const g = data[i + 1];
    const b = data[i + 2];

    // 移除黑色背景
    if (r < BLACK_THRESHOLD && g < BLACK_THRESHOLD && b < BLACK_THRESHOLD) {
      data[i + 3] = 0;
    }
    // 移除天青色背景
    else if (Math.abs(r - CYAN_R) < CYAN_TOLERANCE &&
             Math.abs(g - CYAN_G) < CYAN_TOLERANCE &&
             Math.abs(b - CYAN_B) < CYAN_TOLERANCE) {
      data[i + 3] = 0;
    }
  }

  ctx.putImageData(imageData, 0, 0);
}

// 动画循环
function animate() {
  if (frames.length === 0) return;

  drawFrame(currentFrame);
  currentFrame = (currentFrame + 1) % totalFrames;

  const frameDuration = 1000 / FRAME_RATE;
  animationFrameId = setTimeout(animate, frameDuration);
}

function startAnimation() {
  if (isPlaying || frames.length === 0) return;
  isPlaying = true;
  console.log('[animation] ▶️ 开始播放 (24fps)');
  animate();
}

function stopAnimation() {
  if (!isPlaying) return;
  isPlaying = false;
  clearTimeout(animationFrameId);
  console.log('[animation] ⏹️ 停止播放');
}

// 动态切换人物
async function switchCharacter(characterId) {
  console.log(`[character] 正在切换到: ${characterId}...`);

  // 停止当前动画
  stopAnimation();
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  try {
    // 切换激活人物
    const response = await fetch(`${BACKEND_URL}/api/characters/${characterId}/switch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({})
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const data = await response.json();

    if (!data.ok) {
      throw new Error(data.error || '切换失败');
    }

    // 更新本地配置
    currentCharacter = data.active;
    totalFrames = data.character?.frames_count || 0;
    frames = [];
    currentFrame = 0;

    console.log(`[character] ✓ 已切换到: ${data.character.name} (${totalFrames} 帧)`);

    // 重新加载新人物的帧
    const success = await loadFrames(currentCharacter);

    if (success) {
      console.log('[character] 正在开始新人物的动画...');
      startAnimation();
    } else {
      console.warn(`[character] ⚠️  ${data.character.name} 没有可用的动画帧`);
    }
  } catch (err) {
    console.error('[character] 切换失败:', err);
    // 尝试恢复到之前的人物
    await loadFrames(currentCharacter);
    startAnimation();
  }
}

let currentSessionId = null;  // 保存当前会话 ID

// 初始化：加载保存的会话 ID
async function initializeSession() {
  try {
    const savedSessionId = await invoke('load_current_session_id');
    if (savedSessionId) {
      currentSessionId = savedSessionId;
      console.log('[init] Restored session ID:', currentSessionId);
    } else {
      // 如果没有保存的会话，保持为 null
      // 第一条消息时由后端生成新会话ID
      currentSessionId = null;
      console.log('[init] No saved session, will create new one on first message');
    }
  } catch (err) {
    console.warn('[init] Failed to load current session:', err);
    currentSessionId = null;
  }
}

// 生成会话 ID（使用时间戳格式）
function generateSessionId() {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  const hours = String(now.getHours()).padStart(2, '0');
  const minutes = String(now.getMinutes()).padStart(2, '0');
  const seconds = String(now.getSeconds()).padStart(2, '0');

  // 生成随机后缀（6位十六进制）
  const randomSuffix = Math.floor(Math.random() * 0xFFFFFF).toString(16).padStart(6, '0');

  return `${year}${month}${day}_${hours}${minutes}${seconds}_${randomSuffix}`;
}

// 保存当前会话 ID
async function persistCurrentSessionId() {
  try {
    await invoke('save_current_session_id', { sessionId: currentSessionId });
    console.log('[persist] Saved session ID:', currentSessionId);
  } catch (err) {
    console.warn('[persist] Failed to save session ID:', err);
  }
}

// ── 拖动（右键）────────────────────────────────────────────────────
let dragging = false;
let startMouseX, startMouseY, startWinX, startWinY;
let pendingX, pendingY, movePending = false;
let wasDrag = false; // 区分右键拖动和右键单击
const DRAG_THRESHOLD = 5; // 移动超过 5px 算拖动，否则算单击

 app.addEventListener('mousedown', async (e) => {
  if (e.button === 2) {  // 右键按下
    wasDrag = false;
    dragging = true;
    startMouseX = e.screenX;
    startMouseY = e.screenY;
    try {
      if (win) {
        const pos = await win.outerPosition();
        const sf  = await win.scaleFactor();
        startWinX = pos.x / sf;
        startWinY = pos.y / sf;
      }
    } catch (err) {
      console.warn('[drag] Failed to get window position:', err);
    }
    contextMenu.classList.remove('visible');
  }
});

 window.addEventListener('mousemove', (e) => {
  if (!dragging) return;
  const dx = e.screenX - startMouseX;
  const dy = e.screenY - startMouseY;
  if (Math.sqrt(dx*dx + dy*dy) >= DRAG_THRESHOLD) {
    wasDrag = true; // 超过阈值，标记为拖动
  }
  if (Math.sqrt(dx*dx + dy*dy) < DRAG_THRESHOLD) return; // 未超过阈值，不移动
  if (movePending) return;
  if (!win) return; // Tauri not ready yet
  movePending = true;
  requestAnimationFrame(async () => {
    try {
      const ddx = pendingX - startMouseX;
      const ddy = pendingY - startMouseY;
      await win.setPosition(new LogicalPosition(startWinX + ddx, startWinY + ddy));
    } catch (err) {
      console.warn('[drag] Failed to set window position:', err);
    }
    movePending = false;
  });
});

 window.addEventListener('mouseup', (e) => {
  if (e.button === 2) {
    if (!wasDrag) {
      // 右键单击（非拖动）→ 显示退出菜单
      contextMenu.style.left = e.clientX + 'px';
      contextMenu.style.top  = e.clientY + 'px';
      contextMenu.classList.add('visible');
    }
    wasDrag = false;
    dragging = false;
  }
});

document.addEventListener('mousemove', (e) => {
  pendingX = e.screenX;
  pendingY = e.screenY;
});

// ── 输入框（默认隐藏，左键单击头像出现/隐藏）───────────────────────
avatar.addEventListener('click', (e) => {
  e.stopPropagation();
  inputWrap.classList.toggle('visible');
  if (inputWrap.classList.contains('visible')) {
    msgInput.focus();
  }
});

// 阻止原生右键菜单（防止闪烁）
document.addEventListener('contextmenu', (e) => {
  e.preventDefault();
});

msgInput.addEventListener('keypress', (e) => {
  // keypress 在输入法组合时不会触发，所以输入法输入时 Enter 不会发送
  if (e.key === 'Enter') {
    e.preventDefault();
    sendMessage();
  }
});

// ── 调试日志开关 ────────────────────────────────────────────────
let DEBUG_MODE = false;
let debugLogs = [];

// 动态更新气泡高度
function updateBubbleHeight() {
  const maxHeight = 200;
  const contentHeight = bubbleText.scrollHeight;
  const padding = 24;
  const finalHeight = Math.min(contentHeight + padding, maxHeight);

  const baselineY = 245;
  const newTop = baselineY - finalHeight;

  bubble.style.top = newTop + 'px';
  bubble.style.height = finalHeight + 'px';
  bubble.style.overflowY = finalHeight >= maxHeight ? 'auto' : 'hidden';
}

function debugLog(label, message) {
  if (DEBUG_MODE) {
    const timestamp = new Date().toLocaleTimeString();
    const logMsg = `[${timestamp}] ${label}: ${message}`;
    debugLogs.push(logMsg);
    console.log(logMsg);

    // 显示到气泡中
    if (debugLogs.length > 20) {
      debugLogs = debugLogs.slice(-20); // 只保留最后20条
    }
    const displayText = debugLogs.join('\n');
    showBubble(displayText, true, true);
  }
}

// ── 气泡 ─────────────────────────────────────────────────────────
let bubbleHideTimer = null;

function showBubble(text, persist = false, showCopyBtn = false) {
  if (bubbleHideTimer) { clearTimeout(bubbleHideTimer); bubbleHideTimer = null; }
  bubble.dataset.fullText = text;
  bubbleText.textContent = text;
  bubble.classList.remove('leaving');
  bubble.classList.add('showing');
  bubble.getBoundingClientRect();
  bubble.classList.add('entering');
  bubble.addEventListener('animationend', () => bubble.classList.remove('entering'), { once: true });

  // 动态调整气泡高度：最多200px后用滚动条
  setTimeout(() => {
    const maxHeight = 200; // 最多200px，之后用滚动条
    const contentHeight = bubbleText.scrollHeight;
    const padding = 24; // top + bottom padding
    const finalHeight = Math.min(contentHeight + padding, maxHeight);

    // 气泡底部固定在 245px，向上增长
    const baselineY = 245;
    const newTop = baselineY - finalHeight;

    bubble.style.top = newTop + 'px';
    bubble.style.height = finalHeight + 'px';
    bubble.style.overflowY = finalHeight >= maxHeight ? 'auto' : 'hidden';
  }, 50);

  // 显示/隐藏复制按钮
  copyBtn.style.display = showCopyBtn ? 'block' : 'none';

  if (persist) {
    // 回复信息保留 30 秒，鼠标进入则保持
    const startHideTimer = () => {
      bubbleHideTimer = setTimeout(() => {
        bubble.classList.remove('showing');
      }, 30000);
    };

    startHideTimer();

    // 鼠标进入气泡时，暂停自动消失
    bubble.addEventListener('mouseenter', () => {
      if (bubbleHideTimer) {
        clearTimeout(bubbleHideTimer);
        bubbleHideTimer = null;
      }
    }, { once: false });

    // 鼠标离开气泡时，重新启动消失计时
    bubble.addEventListener('mouseleave', () => {
      startHideTimer();
    }, { once: false });
  }
}

// ── 发送消息（通过 Rust 后端转发 huan-ui 流式）────────────────────────
async function sendMessage() {
  const text = msgInput.value.trim();
  if (!text) return;
  msgInput.value = '';

  // 检测特殊命令
  if (text === '/new') {
    console.log('[/new] Starting new conversation');
    currentSessionId = null;
    console.log('[/new] currentSessionId set to null');
    persistCurrentSessionId();
    console.log('[/new] Persisted null session ID');
    showBubble('已开启新对话', true, true);
    console.log('[/new] New conversation initiated, currentSessionId now:', currentSessionId);
    return;
  }

  // 显示"思考中"气泡（不显示复制按钮）
  showBubble('思考中…', true, false);

  // 记录完整回复用于后续保存和复制
  let fullReply = '';
  let tokenCount = 0;
  let firstTokenShown = false;

  try {
    // 如果没有会话 ID，Rust 后端会创建一个
    console.log('[sendMessage] Calling Rust backend with message:', text, 'Session:', currentSessionId);

    // 创建一个 Promise 来等待流式结束信号
    let streamEndResolve;
    const streamEndPromise = new Promise(resolve => {
      streamEndResolve = resolve;
    });

    // 监听流式事件：每个 token 到达时
    const unlistenStream = await window.__TAURI__.event.listen('chat-stream', (event) => {
      const token = event.payload.token;
      fullReply += token;
      bubbleText.textContent += token;
      tokenCount++;
      debugLog('STREAM', `Token #${tokenCount}: "${token}"`);

      // 第一个 token 到达时，显示复制和语音按钮（如果还没显示的话）
      if (!firstTokenShown) {
        firstTokenShown = true;
        copyBtn.style.display = 'block';
        voiceBtn.style.display = 'block';
      }

      // 每 5 个 token 更新一次气泡高度
      if (tokenCount % 5 === 0) {
        updateBubbleHeight();
      }

      if (tokenCount % 10 === 0) {
        console.log('[stream] Received', tokenCount, 'tokens so far');
      }
    });

    // 监听 think 结束事件：清空"思考中…"，准备显示回复
    const unlistenThinkEnd = await window.__TAURI__.event.listen('chat-think-end', () => {
      debugLog('THINK-END', '思考标签结束，清空"思考中…"');
      bubbleText.textContent = ''; // 清空"思考中…"
      copyBtn.style.display = 'block'; // 显示复制按钮
    });

    // 监听流式结束事件
    const unlistenStreamEnd = await window.__TAURI__.event.listen('chat-stream-end', (event) => {
      debugLog('STREAM-END', `流式结束，总共 ${event.payload.total} 个 token`);
      console.log('[stream] Stream ended, total tokens:', event.payload.total);
      streamEndResolve();
    });

    // 调用 Rust 后端的 chat 命令
    debugLog('SEND', `发送消息: "${text}"`);
    const sessionId = await invoke('chat', { message: text, sessionId: currentSessionId });
    debugLog('BACKEND', `收到 session ID: ${sessionId}`);

    // 等待流式结束信号
    await streamEndPromise;
    debugLog('COMPLETE', `流式结束，共收到 ${tokenCount} 个 token，内容长度: ${fullReply.length}`);

    // 更新当前会话 ID
    if (!currentSessionId) {
      console.log('[sendMessage] Received new session ID from backend:', sessionId);
      currentSessionId = sessionId;
      await persistCurrentSessionId();
      console.log('[sendMessage] Saved new session ID:', currentSessionId);
    } else {
      console.log('[sendMessage] Using existing session ID:', currentSessionId);
    }

    // 保存完整回复用于复制
    bubble.dataset.fullText = fullReply;
    console.log('[sendMessage] Streaming complete. Full reply length:', fullReply.length);

    // 清理事件监听器
    unlistenStream();
    unlistenThinkEnd();
    unlistenStreamEnd();
    console.log('[sendMessage] Event listeners cleaned up');

  } catch (err) {
    console.error('[huanhuan] sendMessage error:', err);
    showBubble(`错误: ${err}`, true, true);
  }
}

// ── 复制气泡内容 ────────────────────────────────────────────
copyBtn.addEventListener('click', async (e) => {
  e.stopPropagation();
  const text = bubble.dataset.fullText;
  if (!text) return;

  try {
    await navigator.clipboard.writeText(text);
    // 显示反馈：按钮文字临时变为"✓"
    const originalText = copyBtn.textContent;
    copyBtn.textContent = '✓';
    setTimeout(() => {
      copyBtn.textContent = originalText;
    }, 1500);
  } catch (err) {
    console.error('[huanhuan] Failed to copy:', err);
  }
});


document.addEventListener('mousedown', (e) => {
  if (e.button === 2) return; // 右键的 mousedown 不关菜单（会在 contextmenu 处理）
  if (!contextMenu.contains(e.target)) contextMenu.classList.remove('visible');
});

// ── 右键菜单：设置 ────────────────────────────────────────────────
const settingsBtn = document.getElementById('settings-btn');

settingsBtn.addEventListener('click', async () => {
  contextMenu.classList.remove('visible');
  try {
    await invoke('open_url', { url: 'http://localhost:8868' });
  } catch (err) {
    console.error('[huanhuan] Failed to open settings:', err);
    showBubble(`打开设置失败: ${err}`, true, false);
  }
});

// ── 右键菜单：切换人物 ────────────────────────────────────────────────
switchCharacterBtn.addEventListener('click', async () => {
  try {
    // 从后端获取人物列表
    const url = `${BACKEND_URL}/api/characters/list`;
    console.log(`[menu] 正在加载人物列表...`);
    console.log(`[menu] 环境: ${typeof window.__TAURI__ !== 'undefined' ? 'Tauri桌面应用' : '网页浏览器'}`);
    console.log(`[menu] BACKEND_URL: ${BACKEND_URL}`);
    console.log(`[menu] 完整请求URL: ${url}`);
    showBubble('加载人物列表中...', false, false);
    // 3秒后自动隐藏加载提示
    setTimeout(() => { if (bubble.classList.contains('showing')) bubble.classList.remove('showing'); }, 3000);

    const response = await fetch(url);
    console.log(`[menu] 收到响应: status=${response.status}, ok=${response.ok}`);

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: 无法加载人物列表`);
    }
    const data = await response.json();
    console.log(`[menu] 加载成功，共 ${data.characters.length} 个人物`);

    // 清空并生成子菜单项
    characterSubmenu.innerHTML = '';
    data.characters.forEach(character => {
      const btn = document.createElement('button');
      btn.textContent = character.name;
      btn.addEventListener('click', async () => {
        console.log(`[menu] 切换到人物: ${character.name} (${character.id})`);
        // 切换人物
        await switchCharacter(character.id);
        // 隐藏菜单
        contextMenu.classList.remove('visible');
        characterSubmenu.classList.remove('visible');
      });
      characterSubmenu.appendChild(btn);
    });

    // 显示子菜单（定位在主菜单右侧）
    const menuRect = contextMenu.getBoundingClientRect();
    const btnRect = switchCharacterBtn.getBoundingClientRect();

    // 子菜单显示在主菜单的右侧
    characterSubmenu.style.position = 'fixed';
    characterSubmenu.style.left = (menuRect.right + 5) + 'px';  // 主菜单右侧 +5px 间距
    characterSubmenu.style.top = btnRect.top + 'px';  // 与按钮顶部对齐
    characterSubmenu.classList.add('visible');

    console.log('[menu] ✅ 人物列表已加载:', data.characters.map(c => c.name).join(', '));
  } catch (err) {
    console.error('[menu] ❌ 加载人物列表失败:');
    console.error('  - 错误类型:', err.name);
    console.error('  - 错误信息:', err.message);
    console.error('  - 完整错误:', err);
    showBubble(`加载失败: ${err.message}`, true, false);
  }
});

// 点击其他地方时隐藏子菜单
document.addEventListener('click', (e) => {
  if (e.target !== switchCharacterBtn && !characterSubmenu.contains(e.target)) {
    characterSubmenu.classList.remove('visible');
  }
});

// ── 右键菜单：调试开关 ────────────────────────────────────────────────
const debugBtn = document.getElementById('debug-btn');
function updateDebugButtonText() {
  debugBtn.textContent = `调试：${DEBUG_MODE ? 'on' : 'off'}`;
}
debugBtn.addEventListener('click', () => {
  DEBUG_MODE = !DEBUG_MODE;
  updateDebugButtonText();
  contextMenu.classList.remove('visible');
  console.log(`[debug] 调试模式: ${DEBUG_MODE ? '开启' : '关闭'}`);
});
updateDebugButtonText();

quitBtn.addEventListener('click', () => invoke('quit_app'));

// ── 页面加载时初始化会话和动画 ────────────────────────────────────
// 由于脚本在页面最后，DOMContentLoaded可能已经触发，所以检查readyState
async function initializeApp() {
  initCanvas();

  // 从服务器加载人物配置
  await initCharacter();

  // 加载当前人物的动画帧
  const framesLoaded = await loadFrames();

  // 如果帧加载成功，启动动画
  if (framesLoaded && frames.length > 0) {
    startAnimation();
  } else {
    console.warn('[init] ⚠️  无法加载动画帧，跳过动画启动');
  }

  // 初始化会话
  await initializeSession();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initializeApp, { once: true });
} else {
  // 文档已加载，直接调用初始化
  initializeApp();
}
