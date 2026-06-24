# Anvil 开发约束

本文件是 `Anvil/` 目录下的顶层执行约束。凡是修改 `Anvil/` 及其子目录内容的代理，都必须先阅读并遵守本文件。

## 项目定位

Anvil 的目标是构建一个**全新、完整、高复用、高解耦、高扩展**的 harness 系统。

默认架构原则：

1. harness / app 严格分离
2. thin adapters
3. 显式 middleware 组合
4. thread-scoped isolation
5. 清晰的 path / persistence / approval / capability contracts

## 总体原则

- 保持 harness-first，不要让 app、shell 或 frontend 反向成为 runtime truth。
- 新增设计优先考虑：
  - 高复用
  - 高解耦
  - 高扩展
  - 可测试
  - 长期可维护
- 不要为了“更先进”而把系统做重。

## 硬性边界

- 必须保持 `harness -> app` **禁止反向依赖**。
- 允许：
  - `app` 依赖 `harness`
  - `frontend` 依赖 `app` 提供的稳定契约
- 禁止：
  - `harness` import `app`
  - 在 router / shell / frontend 中重复实现 harness 逻辑
  - 用 shell 或 frontend 层决定 capability truth

## 文档优先

- 先有架构与契约文档，再有实现。
- 在大型改动前，优先确认：
  - 模块职责
  - 依赖方向
  - 状态契约
  - 路径契约
  - 能力注册与曝光方式
  - 审批与执行控制边界

## 目录治理

推荐结构：

```text
Anvil/
|-- AGENTS.md
|-- README.md
|-- README_zh.md
|-- docs/
|   |-- architecture/
|   |-- adr/
|   |-- guides/
|   `-- implementation/
|-- backend/
|   |-- packages/
|   |   `-- harness/
|   |       `-- anvil/
|   `-- app/
|-- examples/
|-- frontend/
|-- scripts/
`-- plugins/
```

默认仅保留以下 `AGENTS.md`：

- 仓库根目录 `AGENTS.md`
- `backend/packages/harness/AGENTS.md`
- `backend/app/AGENTS.md`
- `frontend/AGENTS.md`（当前 frontend 存在时）

## 发布边界

- 根目录 `skills/` 是 Anvil 初始自带内容，属于公开仓库发布面。
- 公开仓库默认不跟踪用户本地 Anvil Home skill pack、`.anvil/`、`.omx/`、
  调试数据库、内部未来规划和一次性优化日志。
- 新增或更新内置 skill 时，应确认来源、许可证、素材和脚本边界，避免把本地
  缓存、敏感配置或来源不清的素材直接放进主仓库。
- 发布面文档以 `README.md`、`README_zh.md`、`docs/guides/`、`docs/adr/`
  和 `docs/index.md` 为准。

## 代码与文档变更约束

- 先生成 contracts，再生成 runtime，再生成 app，再生成 shell/frontend。
- 先写测试或至少同步写测试。
- 优先拆分清晰模块，不要把调度、协议、路径、审批、工具曝光塞进单一“大总管文件”。
- 任何会影响公开行为的改动，都要同时更新：
  - 相应架构文档
  - 相应 guide / README / example
  - 相应测试

## 默认继承模式

### 直接采用的默认答案

- composition root
- middleware builder
- thin gateway
- path / isolation
- store + checkpointer 基础分层
- frontend wrapper + hooks 边界

### 选择性增强层

可作为局部增强，但不能反向定义整套系统：

- prompt cache 稳定性
- ephemeral turn injection
- tool registry / schema hygiene
- command registry / profile-home
- typed approval / execution control
- network approval 独立服务

## 反模式

禁止默认采用以下做法：

- process-global capability state
- runtime mutation of shared global toolsets
- shell 层反向决定 runtime capability truth
- 在 shell handler 中内嵌 approval / retry / sandbox 流程
- 在一开始就引入过重的 protocol-first app-server
- 让多个风格来源平均混合成模糊架构

## 每次会话开始时必须做的事

- 阅读本文件
- 阅读当前相关架构文档或 ADR / guide
- 阅读当前变更涉及的已有产物
- 只加载与当前任务相关的 references
- 明确当前输出物与退出条件

## 每次会话结束时必须做的事

- 明确本次已完成内容
- 明确是否还有后续入口条件
- 更新相关文档，避免后续会话重复猜测
