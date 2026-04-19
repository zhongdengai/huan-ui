# huanhuan 环境依赖说明

## 项目架构

```
hermes-agent (AI智能体框架)
    ↓ (提供API)
huan-ui (Web UI后端 + 会话管理)
    ↓ (HTTP API at localhost:8868)
huanhuan (Tauri桌面应用 - 本项目)
```

## 依赖关系

### 1. **hermes-agent** (必需)
- 位置: `~/.hermes/hermes-agent/` 或 `~/hermes-agent/`
- 作用: AI模型推理、对话逻辑
- 获取: 需要从上游项目获取
- 配置: 
  - `~/.hermes/config.yaml` - hermes全局配置
  - `~/.hermes/.env` - API密钥（MINIMAX_API_KEY等）
  - `~/.hermes/auth.json` - 认证信息

### 2. **huan-ui** (必需)
- 位置: `./huan-ui/` (本仓库内)
- 作用: Web UI前端 + 会话管理 + API路由
- 自动启动: huanhuan启动时自动启动 `huan-ui/start-huan-ui.sh`
- 端口: 8868 (硬编码)
- 数据存储: (已转移到项目内)
  - 聊天记录: `./huan-ui/webui/sessions/*.json`
  - 用户设置: `./huan-ui/webui/settings.json`
  - 工作区: `./huan-ui/webui/workspaces.json`
  - 索引: `./huan-ui/webui/sessions/_index.json`

### 3. **hermes命令行工具** (可选但推荐)
```bash
pip install hermes-cli
```
用于：
- 启动 hermes gateway: `hermes gateway run`
- 管理认证和配置
- CLI工作流集成

## 安装步骤

### 前置条件
```bash
# Python 3.9+
python3 --version

# Node.js 18+
node --version

# Rust + Cargo (用于Tauri编译)
rustc --version
```

### 1. 安装 hermes-agent
```bash
cd ~
git clone <hermes-agent-repo>
cd hermes-agent
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置 hermes
```bash
# 创建配置目录
mkdir -p ~/.hermes

# 创建 .env（填入API密钥）
cat > ~/.hermes/.env << 'ENVEOF'
MINIMAX_API_KEY=sk_xxxx
DEEPSEEK_API_KEY=sk_xxxx  # 如果需要
ENVEOF'

# 创建或编辑 config.yaml
# 参考: hermes官方文档
```

### 3. 安装项目依赖
```bash
cd huanhuan
npm install
```

### 4. 启动应用
```bash
npm run dev
```

## 启动流程

1. **npm run dev** → 启动 Tauri 应用
2. Tauri 应用启动时，自动执行 `lib.rs` 中的 `start_huan_ui()`
3. `start_huan_ui()` 调用 `huan-ui/start-huan-ui.sh`
4. `start-huan-ui.sh` 检查 hermes-agent 位置，启动 huan-ui
5. huan-ui 启动时自动启动 `hermes gateway`（如果需要）
6. 桌面应用准备好，可以开始聊天

## 环境变量

### huan-ui 相关
```bash
HERMES_WEBUI_PORT=8868              # huan-ui 端口
HERMES_WEBUI_HOST=127.0.0.1         # huan-ui 监听地址
HERMES_WEBUI_STATE_DIR=~/.hermes/webui  # 数据存储位置
HERMES_WEBUI_AGENT_DIR=~/.hermes/hermes-agent  # hermes-agent位置
```

### hermes 相关
```bash
HERMES_HOME=~/.hermes               # hermes 主目录
HERMES_CONFIG_PATH=~/.hermes/config.yaml
```

### API密钥
```bash
MINIMAX_API_KEY=sk_xxxx             # MiniMax API密钥（必需）
MINIMAX_CN_API_KEY=...              # 国内版本（可选）
```

## 网络和代理

### 代理配置
- huanhuan 内部的 reqwest HTTP 客户端**禁用了系统代理**（`.no_proxy()`）
- 这确保 localhost:8868 的请求直接发送，不经过代理
- 外部API请求（如hermes调用OpenAI）仍会使用系统代理

### 端口占用
- 如果 8868 被占用，启动会失败
- 解决: `lsof -i :8868` 找出占用进程，或改PORT（在huan-ui/api/config.py第29行）

## 故障排查

### "huan-ui API error (400 Bad Request)"
- 检查 hermes-agent 是否正常启动
- 检查 MINIMAX_API_KEY 是否设置
- 查看 huan-ui 服务日志

### "Port 8868 is already in use"
- 关闭占用 8868 的进程
- 或临时修改 PORT 配置

### "Could not find hermes-agent"
- 确保 hermes-agent 在 `~/.hermes/hermes-agent/` 或 `~/hermes-agent/`
- 或设置 `HERMES_WEBUI_AGENT_DIR` 环境变量

## 文件结构

```
huanhuan/
├── src/                     # 前端代码（JavaScript）
├── src-tauri/               # Tauri后端（Rust）
├── huan-ui/                 # huan-ui 项目（Web UI）
│   ├── api/                 # 后端路由
│   ├── server.py            # HTTP服务器
│   ├── start-huan-ui.sh     # 启动脚本
│   └── static/              # 前端静态文件
└── DEPENDENCIES.md          # 本文件
```

## 许可证

[你的许可证]
