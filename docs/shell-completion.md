---
description: Enabling Tab completion of alai-index's commands and options in bash, zsh, fish, PowerShell and tcsh.
---

# Shell completion

`alai-index generate-shell-completion SHELL` prints the script that enables
Tab completion, for `bash`, `zsh`, `fish`, `powershell` or `tcsh`.

| Shell | Add to | Line |
|---|---|---|
| bash | `~/.bashrc` | `eval "$(alai-index generate-shell-completion bash)"` |
| zsh | `~/.zshrc`, after `compinit` | `eval "$(alai-index generate-shell-completion zsh)"` |
| fish | | Run `alai-index generate-shell-completion fish > ~/.config/fish/completions/alai-index.fish` once |
| PowerShell | `$PROFILE` | `alai-index generate-shell-completion powershell \| Out-String \| Invoke-Expression` |
| tcsh | `~/.tcshrc` | ``eval "`alai-index generate-shell-completion tcsh`"`` |

alai-index's completion is built on
[argcomplete](https://github.com/kislyuk/argcomplete). When argcomplete's
[global completion](https://github.com/kislyuk/argcomplete#global-completion)
is set up, alai-index is completed without any further setup.
