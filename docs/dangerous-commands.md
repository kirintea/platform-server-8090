# 危险命令参考手册

用于 `command_guard` 的 rules 配置。按平台和用途分类，可直接复制到 YAML。

---

## Linux / macOS

### 文件删除

| 命令 | 危险程度 | 说明 |
|------|---------|------|
| `rm -rf /` | 致命 | 删除根目录 |
| `rm -rf ~` | 致命 | 删除用户主目录 |
| `rm -rf /*` | 致命 | 通配删除根目录下所有内容 |
| `rm -rf .` | 高 | 删除当前目录 |
| `rm -rf *` | 高 | 删除当前目录所有文件 |
| `shred -vfz *` | 高 | 安全覆盖删除 |

### 磁盘/分区

| 命令 | 危险程度 | 说明 |
|------|---------|------|
| `mkfs.*` | 致命 | 格式化分区 (mkfs.ext4, mkfs.ntfs 等) |
| `dd if=* of=/dev/*` | 致命 | 直接写磁盘设备 |
| `fdisk /dev/*` | 高 | 分区操作 |
| `parted *` | 高 | 分区操作 |

### 权限

| 命令 | 危险程度 | 说明 |
|------|---------|------|
| `chmod 777 *` | 中 | 全开权限 |
| `chmod -R 777 *` | 高 | 递归全开权限 |
| `chmod +s *` | 高 | 设置 SUID/SGID |
| `chown root *` | 中 | 变更为 root 所有 |
| `setfacl *` | 中 | ACL 权限修改 |

### 网络/反弹 Shell

| 命令 | 危险程度 | 说明 |
|------|---------|------|
| `*/dev/tcp/*` | 致命 | Bash TCP 反弹 shell |
| `nc -e *` | 致命 | netcat 反弹 shell |
| `ncat -e *` | 致命 | ncat 反弹 shell |
| `socat *` | 高 | 反弹 shell |
| `python -c '*socket*'` | 高 | Python 反弹 shell |
| `perl -e '*socket*'` | 高 | Perl 反弹 shell |
| `ruby -rsocket *` | 高 | Ruby 反弹 shell |

### 远程下载执行

| 命令 | 危险程度 | 说明 |
|------|---------|------|
| `curl *|*bash*` | 致命 | curl 下载并执行 |
| `curl *|*sh*` | 致命 | curl 下载并执行 |
| `wget *|*bash*` | 致命 | wget 下载并执行 |
| `wget -O- *|*sh*` | 致命 | wget 下载并执行 |
| `curl *|*python*` | 高 | curl 下载并执行 |
| `curl *|*perl*` | 高 | curl 下载并执行 |

### 管道执行

| 命令 | 危险程度 | 说明 |
|------|---------|------|
| `*\|*bash*` | 致命 | 任意内容管道到 bash |
| `*\|*sh*` | 致命 | 任意内容管道到 sh |
| `*\|*zsh*` | 致命 | 任意内容管道到 zsh |

### 命令替换

| 命令 | 危险程度 | 说明 |
|------|---------|------|
| `$(*` | 高 | 命令替换 |
| `` `*` `` | 高 | 反引号命令替换 |

### 持久化/提权

| 命令 | 危险程度 | 说明 |
|------|---------|------|
| `crontab -e` | 高 | 写入定时任务 |
| `crontab -` | 高 | 从 stdin 写入 crontab |
| `*> ~/.bashrc` | 高 | 覆盖 shell 配置 |
| `*>> ~/.bashrc` | 中 | 追加到 shell 配置 |
| `*> ~/.bash_profile` | 高 | 覆盖 shell 配置 |
| `*> ~/.zshrc` | 高 | 覆盖 shell 配置 |
| `*> /etc/profile` | 致命 | 覆盖系统级 shell 配置 |
| `*> /etc/crontab` | 致命 | 覆盖系统 crontab |
| `useradd *` | 高 | 添加用户 |
| `adduser *` | 高 | 添加用户 |
| `passwd *` | 高 | 修改密码 |
| `visudo` | 高 | 编辑 sudoers |

### 进程/服务

| 命令 | 危险程度 | 说明 |
|------|---------|------|
| `kill -9 1` | 致命 | 杀 init 进程 |
| `killall *` | 高 | 杀所有同名进程 |
| `pkill *` | 高 | 按模式杀进程 |
| `systemctl stop *` | 中 | 停止服务 |
| `systemctl disable *` | 中 | 禁用服务 |

### 包管理 (远程执行风险)

| 命令 | 危险程度 | 说明 |
|------|---------|------|
| `pip install *` | 低 | Python 包安装 (供应链风险) |
| `npm install *` | 低 | Node 包安装 (供应链风险) |
| `gem install *` | 低 | Ruby 包安装 |

---

## Windows (CMD / PowerShell)

### 文件删除

| 命令 | 危险程度 | 说明 |
|------|---------|------|
| `del /s /q C:\*` | 致命 | 递归删除 C 盘文件 |
| `rd /s /q C:\` | 致命 | 删除 C 盘目录树 |
| `rd /s /q .` | 高 | 删除当前目录 |
| `Remove-Item -Recurse -Force C:\*` | 致命 | PowerShell 递归删除 |
| `rm -Recurse -Force *` | 高 | PowerShell 删除当前目录 |

### 磁盘/分区

| 命令 | 危险程度 | 说明 |
|------|---------|------|
| `format *` | 致命 | 格式化磁盘 |
| `diskpart *` | 高 | 磁盘分区操作 |
| `Clear-Disk *` | 致命 | PowerShell 清除磁盘 |

### 远程下载执行

| 命令 | 危险程度 | 说明 |
|------|---------|------|
| `certutil -urlcache *` | 致命 | certutil 下载文件 |
| `certutil -split -f *` | 致命 | certutil 下载文件 |
| `Invoke-WebRequest *` | 高 | PowerShell 下载 |
| `Invoke-RestMethod *` | 高 | PowerShell 下载 |
| `Start-BitsTransfer *` | 高 | BITS 下载 |
| `(New-Object Net.WebClient).DownloadFile*` | 致命 | .NET 下载 |
| `(New-Object Net.WebClient).DownloadString*` | 致命 | .NET 下载字符串 |

### 远程执行

| 命令 | 危险程度 | 说明 |
|------|---------|------|
| `Invoke-Expression*` | 致命 | PowerShell 远程执行 |
| `iex *` | 致命 | Invoke-Expression 简写 |
| `powershell -encodedcommand *` | 致命 | 编码执行 (隐藏内容) |
| `powershell -enc *` | 致命 | 编码执行简写 |
| `cmd /c *` | 中 | CMD 执行 |

### 注册表

| 命令 | 危险程度 | 说明 |
|------|---------|------|
| `reg delete *` | 高 | 删除注册表项 |
| `reg add *` | 中 | 添加注册表项 |
| `Set-ItemProperty *` | 中 | PowerShell 修改注册表 |

### 用户/权限

| 命令 | 危险程度 | 说明 |
|------|---------|------|
| `net user * /add` | 高 | 添加用户 |
| `net localgroup administrators * /add` | 高 | 添加管理员 |
| `net user *` | 中 | 用户管理 |
| `Add-LocalGroupMember *` | 高 | PowerShell 添加组成员 |

### 服务/进程

| 命令 | 危险程度 | 说明 |
|------|---------|------|
| `taskkill /f /im *` | 高 | 强制杀进程 |
| `Stop-Process -Force *` | 高 | PowerShell 杀进程 |
| `sc delete *` | 高 | 删除服务 |
| `Remove-Service *` | 高 | PowerShell 删除服务 |
| `shutdown /s /t 0` | 高 | 立即关机 |
| `Restart-Computer -Force` | 高 | 强制重启 |

### 网络

| 命令 | 危险程度 | 说明 |
|------|---------|------|
| `netsh advfirewall set allprofiles state off` | 致命 | 关闭防火墙 |
| `netsh interface portproxy *` | 高 | 端口转发 |
| `nslookup *` | 低 | DNS 查询 (信息泄露) |

---

## 配置速查

### 开发环境 (blocklist — 只拦致命)

```yaml
command_guard:
  mode: "blocklist"
  rules:
    # Linux 致命
    - "rm -rf /"
    - "rm -rf ~"
    - "rm -rf /*"
    - "*|*bash*"
    - "*|*sh*"
    - "*/dev/tcp/*"
    - "mkfs.*"
    - "dd if=*of=/dev/*"
    - "*|*python*"
    # Windows 致命
    - "certutil*"
    - "Invoke-Expression*"
    - "iex *"
    - "powershell -enc*"
    - "powershell -encodedcommand*"
    - "rd /s /q C:\\"
    - "format *"
```

### 生产环境 (allowlist — 只放行安全)

```yaml
command_guard:
  mode: "allowlist"
  rules:
    - "ls *"
    - "pwd"
    - "cat *"
    - "head *"
    - "tail *"
    - "grep *"
    - "find *"
    - "wc *"
    - "echo *"
    - "date"
    - "whoami"
    - "env"
    - "ps *"
```

---

## 注意事项

1. **通配符是 fnmatch 语法**：`*` 匹配任意字符，`?` 匹配单个字符，`[seq]` 匹配字符集
2. **匹配目标是命令全文**：`rm -rf /` 会匹配 `rm -rf / && echo done`，因为 fnmatch 的 `*` 跨字符
3. **命令从 JSON 提取**：实际匹配的是 `tool_call.input` 中解析出的 `command` 字段
4. **规则越多越慢**：每条工具调用都会遍历所有规则，保持规则精简
5. **误杀时加白**：blocklist 模式下如果某条命令被误杀，在 rules 里缩小匹配范围
