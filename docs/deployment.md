# 部署:一条命令起容器

这份文件只回答一件事:怎么把它跑起来给人看,以及哪些开关必须由你亲口同意。

## 两种模式,默认是安全的那种

| 模式 | 需要数据? | 需要密钥? | 用途 |
|---|---|---|---|
| `synthetic`(默认) | 不需要 | 不需要 | 演示页面与三条工作流,数据是编的,可公开部署 |
| `local` | 需要 `data/processed/ice_makers_evidence.sqlite` | 可选(只在点「AI 深入分析」时用) | 真实 13.6 万条评论,只能在自己机器或受控环境里跑 |

镜像默认跑 `synthetic`:它**不含任何评论数据**,所以可以把镜像推到任何地方。

## 起容器

```sh
docker compose up --build demo      # http://127.0.0.1:8000
```

真实数据模式(挂载本机数据目录,写入也落在挂载点):

```sh
docker compose --profile local up --build local   # http://127.0.0.1:8001
```

## 三个必须显式打开的开关

代码里默认全部关闭,报错信息会告诉你该加哪个参数:

| 开关 | 不加会怎样 | 为什么默认关 |
|---|---|---|
| `--allow-non-loopback` | 绑 `0.0.0.0` 直接退出 | 桌面入口是纯本机工具,不该靠一个参数就暴露出去 |
| `--acknowledge-local-data` | `--mode local` 直接退出 | 真实评论是本地私有数据,读它要你点头 |
| `--allowed-host` / `WORKBENCH_ALLOWED_HOSTS` | 非 loopback 的 Host 头一律 `400 INVALID_HOST` | 防 DNS rebinding:没有它,一个恶意域名解析到你的端口就能读界面 |

实测(在 0.0.0.0:8799 上跑 `synthetic`):

```
Host 127.0.0.1:8799   -> 200
Host localhost:8799   -> 200
Host demo.example     -> 400   # 没加入白名单时
Host attacker.example -> 400
# 设置 WORKBENCH_ALLOWED_HOSTS=demo.example 之后
Host demo.example     -> 200
Host attacker.example -> 400
```

## 直接起(不用容器)

```sh
python scripts/serve_demo.py --host 0.0.0.0 --allow-non-loopback --port 8000 --mode synthetic
```

## 容器里没有做什么

- 没有 TLS,没有反向代理:这是本地/内网演示,公网请在前面放一层。
- 没有用户体系:页面上的「确认/存疑/驳回」不需要登录,不要在多人共用的公网环境里跑 `local` 模式。
- `local` 模式下 `data/` 必须可写:每次运行写一份 LangGraph checkpoint,每次点判定追加一行到 `data/processed/p7_workbench_verdicts.jsonl`。
- 没有在 CI 里构建过镜像:写这份文件时开发机上没有 Docker,镜像只做了静态审查 + 入口脚本的本机实测。
