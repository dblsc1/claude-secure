# claude-secure：认领 restore-agents 排进队列的任务。
#
# 为什么要有这个东西：Ptyxis 用 `-- <命令>` 起的窗口，左上角的"新建标签/新建
# 窗口"按钮和对应快捷键都是死的（VTE 认为这个窗口属于那条命令，不属于 shell）。
# 所以恢复会话时不再把命令交给窗口，而是：窗口空着起（按钮正常），命令写进
# 队列，由这个 shell 启动时按 $PWD 认领。
#
# 装法：在 ~/.bashrc 末尾加一行
#   . ~/.local/share/claude-secure/job-claim.sh
#
# 已知代价：恢复期间你自己在同一个目录手动开的终端，可能替它把任务领走。
# TTL（默认 120 秒）限制了这个窗口期。

_cs_claim_job() {
  # 任务文件里装的是要 eval 的 shell 命令，所以队列目录本身就是信任边界。
  # 只认 $XDG_RUNTIME_DIR：它是 logind 给本登录会话的私有目录（0700，本人所有）。
  # 不设就什么都不做——退回 /tmp 等于让同机任意用户抢先建出 /tmp/claude-secure，
  # 塞一个第一行写着你 $PWD 的任务进来，你的 shell 就替他执行。
  local base="${XDG_RUNTIME_DIR:-}"
  [[ -n "$base" ]] || return 0
  local dir="$base/claude-secure/jobs"
  local job mine cmd now perm ttl="${CS_JOB_TTL:-120}"
  [[ ! -L "$dir" && -d "$dir" && -O "$dir" ]] || return 0
  perm="$(stat -c %a "$dir" 2>/dev/null)" || return 0
  # 组/他人可写的队列目录不认：别人能往里塞任务。
  [[ "$perm" == 700 ]] || return 0
  now=$(date +%s)
  for job in "$dir"/*.job; do
    # 同理：只认自己拥有的普通文件，不跟符号链接。
    [[ ! -L "$job" && -f "$job" && -O "$job" ]] || continue
    # 过期的任务当作没人要：留着只会被下一个碰巧进同目录的 shell 误领。
    if (( now - $(stat -c %Y "$job" 2>/dev/null || echo "$now") > ttl )); then
      rm -f -- "$job"
      continue
    fi
    [[ "$(head -n 1 "$job")" == "$PWD" ]] || continue
    # rename 是原子的：两个 shell 同时盯上同一个任务，只有一个 mv 得手，
    # 另一个的源文件已经不在，直接跳过。
    mine="$job.$$"
    mv -- "$job" "$mine" 2>/dev/null || continue
    cmd="$(tail -n +2 "$mine")"
    rm -f -- "$mine"
    [[ -n "$cmd" ]] || return 0
    history -s "$cmd"
    eval "$cmd"
    return 0
  done
}

case $- in
  *i*) _cs_claim_job ;;
esac
