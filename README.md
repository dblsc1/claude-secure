# claude-secure

用 [bubblewrap](https://github.com/containers/bubblewrap) 把 Claude Code（以及同类
代理 CLI）关进沙箱的一套启动器：**按身份分权的凭据隔离**、**可选的出网代理**、
**会话/窗口的保存与恢复**。

不是通用容器方案，是一个人的桌面工作流长出来的东西，公开出来给有同样需求的人抄。
所有环境相关的值都在 `config.env` 里，仓库本身不含任何私有路径或账号。

## 它解决什么

代理 CLI 拿着你的全权 GitHub 令牌、能读整个家目录，还能被提示注入。这套脚本：

1. **家目录不是你的家目录** —— 沙箱里的 `$HOME` 是 `~/.local/share/claude-secure/home`，
   宿主的 `~/.ssh`、`~/.aws`、浏览器配置一概看不到。
2. **令牌按身份分** —— `pri`（私有仓可写）和 `pub`（只能往指定公开仓推）各带各的
   细粒度 PAT，通过 git credential helper 按 URL owner 路由。另一个身份的令牌
   根本不挂进来。
3. **pub 的工作区用保留清单** —— 只挂 `CS_PUB_DIR` 一个目录，不是"排除几个敏感
   目录"。漏一条保留清单只是少个功能，漏一条排除清单就是泄露。
4. **不转发 ssh-agent** —— 沙箱里没有任何可以签名的东西。
5. **网络走单 socket** —— `--unshare-net` 加一个 systemd socket 桥，沙箱里只有一个
   到本机代理的入口，没有裸网络栈。

## 它不解决什么

- 不是安全边界的完整替代。`--bind /run/docker.sock` 一开，沙箱内代码等于宿主 root。
- `gh` CLI 每台主机只认一个令牌，身份切换靠重开沙箱，不是靠 `gh auth switch`。
- 宿主侧仍然可以被你自己绕过。它防的是代理跑飞和提示注入，不是防你自己。

## 安装

```sh
git clone https://github.com/<you>/claude-secure ~/src/claude-secure
ln -s ~/src/claude-secure/bin/*      ~/.local/bin/
ln -s ~/src/claude-secure/libexec/*  ~/.local/libexec/
cp ~/src/claude-secure/config.env.example ~/.config/claude-secure/config.env
$EDITOR ~/.config/claude-secure/config.env

# 令牌：两个身份各两个文件，0600
install -d -m 0700 ~/.local/share/claude-secure/credentials
printf '%s' "<pri 个人仓 PAT>"  > ~/.local/share/claude-secure/credentials/pri-personal.token
printf '%s' "<pri 组织仓 PAT>"  > ~/.local/share/claude-secure/credentials/pri-org.token
printf '%s' "<pub 公开仓 PAT>"  > ~/.local/share/claude-secure/credentials/pub-personal.token
printf '%s' "<pub 组织仓 PAT>"  > ~/.local/share/claude-secure/credentials/pub-org.token
chmod 600 ~/.local/share/claude-secure/credentials/*

# 出网桥（可选，不装就是沙箱完全无网）
cp systemd/*.service systemd/*.socket systemd/*.timer ~/.config/systemd/user/
systemctl --user enable --now claude-secure-proxy.socket
```

`claude-pri` / `claude-pub` 就是带身份的启动器；`claude-secure --shell` 给你一个
沙箱内的交互 shell，调试挂载用。

## Docker

启动时问一次：

```
1) 放行全量 docker.sock —— 沙箱内代码等于拿到宿主机 root，能逃逸出沙箱
2) rootless（需要 claude-secure-bwrap + AppArmor 配置）
3) 不给 docker（默认，最安全）
```

`restore-agents` 恢复出来的窗口弹不了菜单（ptyxis 是单实例 GApplication），
模式取自 `~/.config/claude-secure/docker-mode`（`full` / `rootless` / `none`，
文件不存在按 `none`）。写 `full` 等于给所有恢复出来的沙箱一张宿主 root 的长期
通行证——这是一个明确的、要自己权衡的授权动作。

## 会话与窗口恢复

`ai-workspace save <名字>` 记下当前所有代理会话和终端窗口位置，
`ai-workspace open <名字>` 原样开回来（窗口位置依赖一个 GNOME 扩展，
用 `CS_LAYOUT_DBUS_NAME` 指定它的 DBus 名；没装就只恢复会话）。

## 推送闸门（可选）

`libexec/claude-secure-push-gate` 是一个 PATH 上的替身 `git`：只允许往
`CS_PUBLIC_REPOS` 白名单里的仓推，扫提交信息和 diff 里的商业术语，跑
`CS_SCAN_SCRIPT` 做密钥扫描。做成替身 `git` 而不是 git hook，是因为 hook 能被
`--no-verify` 绕过。默认没接线，要用就照 `bin/claude-secure` 里的注释把三行
取消注释。

## License

MIT
