# 球赛实时监控

多源球赛数据监控，规则匹配后飞书推送通知。支持篮球逐节得分（Q1-Q4）和足球海量联赛。

## 环境要求

- Python 3.11+
- Windows / Linux / macOS

## 快速开始

```bash
# 1. 创建虚拟环境
python -m venv venv

# 2. 安装依赖
# Windows:
venv\Scripts\pip install -r requirements.txt
# Linux / macOS:
./venv/bin/pip install -r requirements.txt

# 3. 配置
# 编辑 config.yaml，填写飞书 webhook 和 API key

# 4. 启动
# Windows:
venv\Scripts\python run.py
# Linux / macOS:
./venv/bin/python run.py
```

打开 http://localhost:8001 管理规则和查看赛程。

## 配置说明

```yaml
# config.yaml
datasource:
  type: "apisports"          # apisports / espn / dongqiudi
  poll_interval_seconds: 300 # 轮询间隔（秒）

feishu:
  webhook_url: ""            # 飞书机器人 Webhook 地址

apisports:
  keys:                      # API-Sports key，支持多个轮询
    - "your-api-key"
```

## 数据源

| 数据源 | 篮球 | 足球 | 逐节得分 | 费用 |
|--------|------|------|---------|------|
| apisports | 95 场/天 | 259 场/天 | ✅ Q1-Q4 | 需要 key |
| espn | 6 场/天 | 1 场/天 | ✅ | 免费 |
| dongqiudi | 15 场/天 | 97 场/天 | ❌ | 免费 |

## 规则类型

| 类型 | 说明 | 示例 |
|------|------|------|
| quarter_parity | 指定节次奇偶 | Q1、Q2 都是单数 |
| quarter_sequence | 多节序列 + 触时节 | Q1单 Q2单 → Q4通知 |
| total_score | 总得分比较 | 总分 > 200 |
| quarter_diff | 单节分差 | Q1 分差 > 5 |

## 测试飞书连通性

```bash
python test_feishu.py
```

每 5 分钟检测一次，命中规则推飞书。
