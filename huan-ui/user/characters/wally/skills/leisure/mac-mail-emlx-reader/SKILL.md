---
name: mac-mail-emlx-reader
description: 直接读取 macOS Mail.app 存储的 Gmail .emlx 邮件文件（无需 himalaya 或 Gmail API）
version: 1.0.0
author: hermes
license: MIT
metadata:
  hermes:
    tags: [Email, macOS, Gmail, Mail.app, EMLX]
prerequisites:
  system: macOS
  permissions: Terminal 需要"完全磁盘访问权限"（系统设置 → 隐私与安全性 → 完全磁盘访问权限 → 加 Terminal）
  other: 读 Mail.app 数据前需先授权终端完全磁盘访问
---

# Mac Mail EMLX 邮件读取器

## 核心发现（关键经验）

**Mail.app + Gmail 的存储结构（踩坑关键）：**

```
~/Library/Mail/V10/
├── MailData/                      # SQLite 索引数据库
│   └── "Envelope Index"           # 邮件元数据（subject, sender, date），但不含正文
├── <Gmail-UUID-A27DECAF...>/     # 第一个 Gmail 账户
│   ├── INBOX.mbox/               # INBOX（可能为空，Gmail 默认不下载到本地）
│   └── [Gmail].mbox/
│       ├── 所有邮件.mbox/         # ⭐ 邮件实体在这里！
│       │   └── <UUID>/Data/Messages/*.emlx
│       ├── 已发邮件.mbox/
│       └── ...
└── <Gmail-UUID-C4ED8F57...>/     # 第二个 Gmail 账户（可能是同一个）
    └── [Gmail].mbox/
        └── 所有邮件.mbox/...     # 另一个账户的邮件
```

**坑点：**
- SQLite Envelope Index 有邮件元数据但没有正文内容
- Gmail 邮件默认"仅在服务器上保留"（不下载 .emlx 到本地）
- 即使邮箱显示有邮件，`~/Library/Mail/V10/<UUID>/INBOX.mbox/` 可能为空
- 真正的邮件在 `[Gmail].mbox/所有邮件.mbox/<UUID>/Data/Messages/*.emlx`
- Mail.app 能显示是因为实时从 Gmail 云端拉流，不是本地有完整数据

**解决方案：**
- 方案 A：让 Mail.app 下载完整邮件（设置 → 账户 → Gmail → 高级 → 勾选"下载完整邮件"）
- 方案 B：直接读已有的 .emlx 文件（如果邮件已部分下载）

## EMLX 文件格式

第一行是邮件总字节数（字符串形式），后面是完整的邮件内容（headers + body）。

```python
import quopri, re

with open('/path/to/xxx.emlx', 'rb') as f:
    raw = f.read()

lines = raw.decode('utf-8', errors='replace').split('\n', 1)
body = lines[1]  # 第一行是长度，不是邮件内容

# quoted-printable 解码
try:
    decoded = quopri.decodestring(body.encode('utf-8')).decode('utf-8', errors='replace')
except:
    decoded = body

# HTML tag 去除
text = re.sub(r'<[^>]+>', ' ', decoded)
text = re.sub(r'&nbsp;', ' ', text)
text = re.sub(r'\s+', ' ', text).strip()
```

## 快速查找特定邮件

### 1. 用 SQLite 搜索 subject（最快）
```bash
sqlite3 ~/Library/Mail/V10/MailData/"Envelope Index" \
  "SELECT m.ROWID, m.date_sent, s.subject FROM messages m
   JOIN subjects s ON m.subject = s.rowid
   WHERE s.subject LIKE '%关键词%' LIMIT 10;"
```

### 2. 找某个 Gmail 账户的所有邮件目录
```bash
ls ~/Library/Mail/V10/
# 找包含 [Gmail].mbox 的目录（每个目录 = 一个邮箱账户）
```

### 3. 按日期筛选 .emlx 文件
```bash
ls -la ~/Library/Mail/V10/<UUID>/\[Gmail\].mbox/所有邮件.mbox/<SUB-UUID>/Data/Messages/ | grep "Apr 11"
```

## 完整流程示例

```python
# 找到"天琴AI日报 · 2026年4月11日"的邮件
import sqlite3, quopri, re, subprocess

mail_dir = '/Users/jiaqi/Library/Mail/V10'

# Step 1: SQLite 搜索
db = sqlite3.connect(f'{mail_dir}/MailData/Envelope Index')
cursor = db.execute("""
    SELECT m.ROWID, m.date_sent, s.subject
    FROM messages m JOIN subjects s ON m.subject = s.rowid
    WHERE s.subject LIKE '%天琴AI日报%' AND s.subject LIKE '%4月11日%'
    LIMIT 5;
""")
results = cursor.fetchall()
print(results)  # [(247, 1775880068, '🛰️ 天琴AI日报 · 2026年4月11日')]

# Step 2: ROWID 247 → 找到对应 .emlx 文件路径（需知道 mailbox UUID）
# 邮件在 <UUID>/[Gmail].mbox/所有邮件.mbox/<SUB-UUID>/Data/Messages/247.emlx

# Step 3: 读取并解码
with open(f'{mail_dir}/<UUID>/[Gmail].mbox/所有邮件.mbox/<SUB-UUID>/Data/Messages/247.emlx', 'rb') as f:
    raw = f.read().decode('utf-8', errors='replace')
body = raw.split('\n', 1)[1]
decoded = quopri.decodestring(body.encode()).decode('utf-8', errors='replace')
text = re.sub(r'<[^>]+>', ' ', decoded)
print(text)
```

## 查找多个 Gmail 账户

```bash
find ~/Library/Mail/V10/ -mindepth 2 -maxdepth 2 -name "[Gmail].mbox" 2>/dev/null
# 输出：~/Library/Mail/V10/A27DECAF.../[Gmail].mbox
#       ~/Library/Mail/V10/C4ED8F57.../[Gmail].mbox
```

## 已知问题

- Gmail 邮件只有部分下载到本地（.partial.emlx 或无 .emlx），内容不完整
- SQLite 里邮件存在但 .emlx 文件找不到 = 邮件在云端，本地只有索引
- 需给 Terminal 完全磁盘访问权限才能读 ~/Library/Mail/
