# 部署:一条命令起容器

这份文件只回答一件事:怎么把它跑起来给人看,以及哪些开关必须由你亲口同意。

## 两种模式,默认是安全的那种

| 模式 | 需要数据? | 需要密钥? | 用途 |
|---|---|---|---|
| `bundled`(默认) | 不需要(随仓库自带) | 可选 | 真实评论的 3 商品子集:11,958 条,10.9 MB |
| `synthetic` | 不需要 | 不需要 | 数据全是编的,用来看页面骨架 |
| `local` | 需要 `data/processed/ice_makers_evidence.sqlite` | 可选(只在点「AI 深入分析」时用) | 真实 13.6 万条评论,只能在自己机器或受控环境里跑 |

镜像默认跑 `synthetic`:它**不含任何评论数据**,所以可以把镜像推到任何地方。

## 下载即用(不需要数据集,不需要密钥)

仓库自带 `data/public/ice_makers_public.sqlite`:3 个商品、11,958 条真实评论、10.9 MB。克隆下来直接起:

```sh
docker compose up --build demo      # http://127.0.0.1:8000
```

或者不用容器:

```sh
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python scripts/serve_demo.py --port 8000 --mode bundled
```

默认模式就是 `bundled`。页面上的三条工作流、证据卡、人工核对记录全部可用;
只有「AI 深入分析」按钮需要密钥——设置环境变量 `DEEPSEEK_API_KEY` 后重启即可,
不设置时按钮置灰,其它功能不受影响。

这份子集是**固定演示子集,不是数据分发通道**:没有批量导出接口,也不会加。
出处、类目与抽样范围写在页面上,删除请求的渠道见 `docs/data-use-and-publication.md`。
完整类目库(165 MB)与原始 gzip 仍只在本机。

## 访问者自带密钥(部署方不花一分钱)

部署时可以不配置 `DEEPSEEK_API_KEY`。这样只有「AI 深入分析」需要密钥:

1. 访问者点按钮,页面弹出一个输入框(密码类型)请他自己填;
2. 填完只存在**当前标签页的 sessionStorage**,关标签即清除;
3. 每次分析以 `X-Insight-Key` 请求头带过去,服务端只为那一次调用构造客户端,
   用完全部丢弃——不落盘、不进日志、不进 trace、不回显进响应;
4. 换一张卡继续用同一把钥匙,不用重复输入。

服务端的防线:

| 防线 | 行为 |
| --- | --- |
| 密钥形状校验 | 只接受 `[A-Za-z0-9_.-]{8,200}`;不合格返回 `422 INVALID_INPUT`,且**不回显**收到的内容 |
| 限速 | 同一 IP 每 10 分钟最多 10 次分析调用,超出返回 `429 RATE_LIMITED`(进程内计数,够挡住手滑与热循环;真要配额请放在反向代理) |
| 无密钥 | 返回 `200 UNAVAILABLE / MODEL_NOT_CONFIGURED`,页面提示填自己的 key |
| 结果缓存 | 成功结果按(运行令牌, 证据卡)缓存,重复点不再调用;缓存里没有任何密钥 |

实测(服务器未设密钥,只用访问者的 key):`no key -> UNAVAILABLE MODEL_NOT_CONFIGURED`;
带上访问者 key 后 `READY`,1 次调用、读取 5 条评论、3 个具体点,响应里搜不到该密钥。

想彻底关掉这条路(例如内网部署只用自己的 key):设置环境变量 `DEEPSEEK_API_KEY` 即可,
页面会优先用部署方的密钥,不再弹输入框。

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
