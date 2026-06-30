# 业法协同系统

> 国有企业法务合规"业务-法务协同 + AI 辅助审核 + 知识自动沉淀"前后端分离 Web 闭环平台。
> 定位：课程实验交付 + 企业生产部署双轨。

---

## 文档索引

| 序号 | 文档 | 说明 |
|------|------|------|
| 01 | [系统需求分析](01-系统需求分析.md) | 背景、功能/非功能需求、权限模型、校验规则 |
| 02 | [总体设计](02-总体设计.md) | 架构、模块划分、OOP 类图、数据结构 |
| 03 | [详细设计](03-详细设计.md) | 各模块详细设计 + 核心算法 |
| 04 | [测试分析报告](04-测试分析报告.md) | 测试方案、用例、结果、问题修复 |
| 05 | [用户手册](05-用户手册.md) | 安装、配置、使用、FAQ |
| 06 | [开发日志](06-开发日志.md) | 开发过程记录、关键决策、问题与解决 |
| — | [设计文档（原始）](2026-06-25-业法协同系统-design.md) | 完整设计说明书（v2.0） |

---

## 实验环境

| 项 | 配置 |
|---|---|
| 操作系统 | Windows 10/11 |
| Python 版本 | 3.9+（开发环境 3.14） |
| IDE | VS Code / Trae |
| 后端框架 | Flask（`pip install flask`） |
| 前端 | 原生 HTML5 + CSS3 + JavaScript（fetch API），无构建工具 |
| 大模型 | 豆包 Ark `ark-code-latest`（OpenAI 兼容协议），生产可换私有部署 |
| 标准库依赖 | json / re / math / os / datetime / collections / abc / hashlib / urllib / logging |

---

## 快速开始

### 1. 安装依赖

```bash
pip install flask
```

### 2. 启动后端

```bash
python -m backend.app
```

启动后访问 `http://127.0.0.1:5000/`。

### 3. 演示账号

| 角色 | 账号 | 密码 | 权限 |
|------|------|------|------|
| 业务 | business01 | 123456 | 咨询、上传、提交工单 |
| 法务 | legal01 | 123456 | 审核、校验、确认入库 |
| 管理员 | admin01 | 123456 | 知识库管理、账户、归档 |

### 4. 运行测试

```bash
# 端到端集成测试（需先启动后端）
python -m tests.integration_test

# 单元冒烟测试
python -m tests.smoke_test

# 大模型连通性测试
python -m backend.ai.llm_client
```

---

## 项目结构

```
业法协同系统/
├─ backend/                  # Flask 后端
│   ├─ app.py                # 入口，依赖注入
│   ├─ config.py             # 配置（含大模型）
│   ├─ ai/                   # AI 引擎
│   │   ├─ analyzer.py       # 双算法（KeywordMatcher + TF-IDF）
│   │   ├─ llm_client.py     # 大模型客户端（OpenAI 兼容）
│   │   ├─ orchestrator.py   # RAG+LLM 编排器
│   │   └─ routes.py
│   ├─ auth/                 # 账户与权限
│   ├─ business/             # 业务门户
│   ├─ legal/                # 法务工作台（含校验器）
│   ├─ knowledge/            # 知识库管理
│   ├─ law_search/           # 法律条文搜索（FLK API）
│   ├─ report/               # 公文生成
│   ├─ archive/              # 归档导出
│   └─ models/               # 共享数据模型
├─ frontend/                 # 前端（三套界面）
│   ├─ login.html / business.html / legal.html / admin.html
│   └─ static/               # 公共样式与脚本
├─ data/                     # 运行数据
│   ├─ accounts.json         # 账户
│   ├─ knowledge_base.json   # 知识库（核心）
│   ├─ work_orders/          # 法务工单
│   └─ archive/              # 归档包
├─ tests/                    # 测试
│   ├─ integration_test.py   # 端到端集成测试
│   └─ smoke_test.py         # 单元冒烟测试
├─ docs/                     # 文档
└─ FAQ_四部分版_最终.md       # 知识库源文件
```

---

## 核心能力

1. **三角色权限**：业务提交、法务审核校验入库、管理员直管知识库
2. **RAG + LLM**：双算法检索（关键词粗筛 + TF-IDF 精排）+ 大模型生成四段式法务意见
3. **合同审核**：六阶段流水线独立模块，支持多轮问答与会话持久化
4. **法律条文搜索**：基于国家法律法规数据库 (flk.npc.gov.cn) 的 FLK API，支持法条搜索与验证
5. **6 项校验**：问题完整性、四段式完整性、法条格式、标签、重复、问题格式
6. **工单留痕**：提交人/时间、AI 结论、审核人/时间、入库来源全链路追溯
7. **归档导出**：法务确认后打包归档（工单+对话+结论）
