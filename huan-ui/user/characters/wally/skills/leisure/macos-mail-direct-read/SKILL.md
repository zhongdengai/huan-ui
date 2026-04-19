---
name: macos-mail-direct-read
description: Read macOS Mail.app emails directly from ~/Library/Mail/ via Terminal, without IMAP/SMTP or himalaya. Useful when Full Disk Access is granted.
version: 1.0.0
author: hermes
license: MIT
metadata:
  hermes:
    tags: [macOS, Email, Apple Mail, CLI]
prerequisites:
  - System permission: Terminal must have Full Disk Access (Settings → Privacy & Security → Full Disk Access → add Terminal)
  - Mail.app configured with the email account
  - No additional tools needed — uses built-in shell commands
---

# macOS Mail Direct Read

读取 macOS Mail.app 的邮件数据，无需配置 IMAP/SMTP 或安装 himalaya。

## 邮件存储结构

```
~/Library/Mail/V10/<UUID>/
├── INBOX.mbox/
├── [Gmail].mbox/              ← Gmail 标签（如已归档、已发送等）
│   ├── All Mail.mbox/
│   ├── Sent Mail.mbox/
│   ├── Drafts.mbox/
│   └── ...
```

每个 `.mbox` 内部结构：
```
INBOX.mbox/
├── Info.plist
└── <UUID>/                    ← 邮件存储卷
    └── Data/
        └── Messages/
            ├── 1.emlx
            ├── 2.emlx
            └── ...
```

## .emlx 文件格式

`.emlx` 是 Apple Mail 的邮件格式：
- **第一行**：邮件内容的**字节数**（字符串格式，如 `47491`），仅作计数用
- **后面所有内容**：原始邮件（headers + body，multipart 或 HTML）

❗ **注意**：`head -c 2000` 读出来的第一行是字节数字，不是邮件内容。要用 `tail -n +2` 跳过第一行。

```bash
# 正确读取邮件内容（跳过第一行字节数）
tail -n +2 "$FILE" | head -c 5000
```

subject 可能经过 base64 编码，格式为 `=?utf-8?B?...?=`：
```python
python3 -c "import base64; print(base64.b64decode('base64字符串==').decode('utf-8'))"
```

### 邮件内容解码（重要！）

邮件 body 通常使用 **quoted-printable** 编码（`=` 后面跟十六进制），而不是 base64。直接 grep 搜中文字符会失败，因为 "天琴" 存储为 `=E5=A4=A9=E7=90=B4`。

**Python 完整读取流程（推荐）**：
```python
import quopri, re

with open('/path/to/247.emlx', 'rb') as f:
    raw = f.read()

lines = raw.decode('utf-8', errors='replace').split('\n', 1)
body = lines[1] if len(lines) > 1 else ''
header_end = body.find('\n\n')
email_body = body[header_end+2:]

# 解码 quoted-printable
decoded = quopri.decodestring(email_body.encode('utf-8')).decode('utf-8', errors='replace')

# 去掉 HTML 标签（可选）
text = re.sub(r'<[^>]+>', ' ', decoded)
text = re.sub(r'\s+', ' ', text).strip()
print(text)
```

## 多账户注意事项

`~/Library/Mail/V10/` 下有**多个 UUID 目录**，每个对应一个邮件账户。不要只扫第一个 UUID，如果找不到邮件，检查其他 UUID。

```
~/Library/Mail/V10/
├── <UUID-A>/  ← 账户 A（可能是主账户）
├── <UUID-B>/  ← 账户 B（可能是同一个 Gmail 的不同同步状态）
└── MailData/  ← 索引数据（Envelope Index 可能是空的）
```

同一个 Gmail 地址可能在两个 UUID 里都有邮件（一个存实际邮件，一个可能是缓存）。

`.partial.emlx` 是 Gmail 未完全下载的邮件，grep 时要排除：
```bash
grep -v partial.emlx
```

## 查找邮件文件

```bash
# 找 INBOX（如果里面有 Data/Messages 的话）
find ~/Library/Mail/V10/ -path "*/INBOX.mbox/*/Data/Messages/*.emlx" 2>/dev/null | head -5

# 找 All Mail（最完整的存档）
find ~/Library/Mail/V10/ -path "*[Gmail].mbox/All Mail.mbox*/Data/Messages/*.emlx" 2>/dev/null | wc -l

# 找指定邮箱的路径
ACCOUNT_UUID=$(ls ~/Library/Mail/V10/ | grep -v MailData)
echo $ACCOUNT_UUID
```

## 读取邮件内容

```bash
# 完整读取（第一行是字节数，要跳过）
FILE=~/Library/Mail/V10/.../Messages/51.emlx
tail -n +2 "$FILE" | head -c 5000

# 或直接 tail（去掉第一行）
tail -n +2 "$FILE"

# 查看前 N 封
find .../Messages/ -name "*.emlx" 2>/dev/null | sort | head -10
```

## 解析邮件

邮件解码涉及：
- `quoted-printable` 编码（`=` 开头的十六进制）
- `UTF-8` subject base64：`=?utf-8?B?...?=` → 用 Python 解码
- HTML body：需要 `text/html` 部分提取

## ⚠️ Gmail 特殊行为

Gmail 账户的 **INBOX 通常为空或稀疏**，实际邮件存在 `[Gmail].mbox/所有邮件.mbox/` 里。

同时 Gmail 可能只同步 headers 到本地（`.emlx` 只有几十字节 flags），body 在云端。特征：
- `INBOX.mbox` 里完全找不到邮件
- `ls Messages/` 目录是空的
- SQLite Envelope Index 里能搜到 subject，但 .emlx 文件内容几乎为空

**解决方法**：在 Mail.app 设置里勾选"下载完整邮件"，或直接用 Gmail API。

## SQLite 元数据查询（关键！）

Mail.app 把所有邮件的元数据存在 SQLite 里，即使 body 没下载也能搜到 subject/date/sender：

```bash
# 查所有表格
sqlite3 ~/Library/Mail/V10/MailData/"Envelope Index" ".tables"

# 搜索 subject（最常用）
sqlite3 ~/Library/Mail/V10/MailData/"Envelope Index" \
  "SELECT s.subject FROM messages m JOIN subjects s ON m.subject = s.rowid WHERE s.subject LIKE '%关键词%' LIMIT 20;"

# 查某封邮件的 ROWID 和日期（date_sent 是 Unix 时间戳）
sqlite3 ~/Library/Mail/V10/MailData/"Envelope Index" \
  "SELECT m.ROWID, m.date_sent, s.subject FROM messages m JOIN subjects s ON m.subject = s.rowid WHERE s.subject LIKE '%4月11日%' LIMIT 5;"
```

## 完整工作流（推荐）

1. **搜 subject** → SQLite 查询 `SELECT m.ROWID, s.subject FROM messages m JOIN subjects s ON m.subject = s.rowid WHERE s.subject LIKE '%关键词%'`
2. **找文件路径** → ROWID 对应 `[Gmail].mbox/所有邮件.mbox/.../Data/Messages/<ROWID>.emlx`
3. **读内容** → Python 脚本解码 quoted-printable（见上方代码）
4. **路径找错了？** 多个 UUID 目录都要扫，同一个 Gmail 可能映射到不同 UUID

## 注意事项

1. **第一行是字节数**，`head -c 2000` 读到的第一行是数字字符串，不是邮件内容
2. **Gmail 标签**映射为 `[Gmail].mbox/` 下的子文件夹
3. **INBOX 为空很常见**：Gmail 策略是所有邮件存在 `[Gmail].mbox/所有邮件.mbox`，不是 INBOX
4. **部分下载**：`.partial.emlx` 是 Gmail 未完全下载的邮件，grep 时排除
5. **搜不到中文内容**：grep 直接搜 `.emlx` 文件里的中文会失败，因为是 quoted-printable 编码存的不是明文
6. **body 在云端时**：用 SQLite 确认邮件存在，再用 Python 读 .emlx 确认本地是否有内容

## 快速验证

```bash
# 确认 Terminal 有权限
ls ~/Library/Mail/

# 看有多少封邮件
find ~/Library/Mail/V10/ -name "*.emlx" 2>/dev/null | wc -l

# 读一封测试
FILE=$(find ~/Library/Mail/V10/ -name "*.emlx" 2>/dev/null | grep -v partial | head -1)
echo "=== 邮件路径: $FILE ==="
tail -n +2 "$FILE" | head -c 1000
```
