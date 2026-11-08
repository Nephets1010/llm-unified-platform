# Contributing Guide

感谢对 LLM Unified Platform 的关注！欢迎提交 Issue 和 Pull Request。

---

## 开发环境

```bash
# 1. 克隆仓库
git clone <repo-url> && cd llm-unified-platform

# 2. 创建虚拟环境
python -m venv .venv && source .venv/bin/activate

# 3. 安装依赖
pip install -e ".[dev]"

# 4. 配置环境变量
cp .env.example .env

# 5. 启动本地服务
docker compose up -d postgres redis
alembic upgrade head
uvicorn llm_platform.main:app --reload
```

## 测试

```bash
# 全量测试（243+ 用例，必须全部通过）
python -m pytest tests/ -q

# 代码检查（0 error）
ruff check src/ tests/

# 仅跑单元测试
python -m pytest tests/unit/ -q

# 仅跑集成测试
python -m pytest tests/integration/ -q
```

## PR 规范

1. **分支命名**：`feature/xxx`、`fix/xxx`、`docs/xxx`
2. **提交信息**：使用祈使句，清晰描述变更（如 `feat: add Ollama adapter timeout handling`）
3. **测试要求**：新功能必须附带测试；Bug 修复必须附带回归测试
4. **代码检查**：PR 必须通过 `ruff check` + 全量 `pytest`
5. **密钥安全**：gitleaks 会自动扫描，严禁提交任何密钥/Token/密码

## 代码风格

- 遵循 Google Python Style Guide
- 使用类型注解（Python 3.11+ 语法）
- 模块/类/公共方法必须有 docstring
- SQL 语句仅在 `db/repositories/` 中出现
- 错误处理使用统一错误信封（`errors.py`）

## 架构约束

- 依赖方向严格单向：`api → gateway → routing → adapters`
- 监控模块（`monitoring/`）不得反控业务逻辑
- 所有密钥通过环境变量注入，不得硬编码

## 行为准则

- 尊重每一位贡献者
- 以建设性态度讨论技术问题
- 关注代码质量，不追求速度而牺牲可维护性
