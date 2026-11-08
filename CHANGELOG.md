# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.1.0] - 2026-11-08

### Added

#### 核心能力
- **多厂商适配器**：OpenAI / DeepSeek / Qwen / Anthropic / Ollama 统一 ChatRequest/ChatResponse 协议
- **动态路由**：4 种策略（成本优先/延迟优先/加权轮询/健康感知）+ 熔断器（closed/open/half_open）
- **限流与预算**：租户/Key/模型三级 Redis Lua 令牌桶 + 月度预算 80%/100% 熔断
- **统一 API**：`/v1/chat/completions`（含 SSE 流式）、`/v1/models`、`/v1/embeddings`（OpenAI 兼容）
- **鉴权与审计**：API Key（SHA-256 哈希存储）+ JWT 三级角色（admin/consumer/evaluator），fail-closed
- **评估体系**：200 条内置基准集（5 维度）、多指标（accuracy/F1/ROUGE-L/latency/tokens/cost）、多模型对比、checksum 防篡改
- **MCP 工具网关**：工具注册/调用/审计（source=mcp 落 call_log）

#### 工程化
- **可观测性**：Prometheus 全量指标（QPS/延迟/错误/Token/成本/熔断/限流/预算/评估）
- **账单系统**：按模型/项目聚合、CSV/JSON 导出
- **告警系统**：5 组告警规则（错误率/延迟/熔断/预算/call_log）
- **Grafana 看板**：3 张（网关概览/成本预算/韧性路由）
- **配置中心**：版本化配置 + Redis pub/sub 热更新（≤5s）+ 一键回滚
- **灰度发布**：canary_weight 权重分流 + 租户/Key 白名单粒度
- **call_log 缓冲**：异步批量写入 + 指数退避重试（3 次）+ 重试缓冲
- **任务调度**：评测任务状态机 + 队列 + Worker + 任务级超时
- **CI/CD**：GitHub Actions（lint/test/gitleaks/Docker build）+ 测试报告 artifact

#### 文档与工具
- 架构设计文档（分层图/类图/序列图）
- API 接口契约（统一错误信封 + 错误码表）
- 部署方案（Docker Compose + 单机服务器）
- 运维手册（巡检/告警处置/灰度发布/故障排查）
- 一键演示脚本（demo_quickstart.sh）
- 故障演练脚本（failover_drill.sh / canary_drill.sh）
- 种子数据生成脚本（seed_call_log.py / seed_sample_data.py）

### Security
- API Key 仅存 SHA-256 哈希，明文仅创建时返回一次
- JWT fail-closed：空 secret 拒绝启动
- 敏感字段自动脱敏（utils/masking.py）
- gitleaks 密钥扫描集成 CI
