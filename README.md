# Game Localization Engineering Toolkit

游戏汉化工程实践与可复用工具集

## 项目简介

本仓库用于整理游戏汉化工程中的技术方案、自动化流程、验证方法和可复用开发框架。

## 实践项目

Higurashi When They Cry Hou — Chapter 8: Matsuribayashi

- 目标文本槽：28,014
- 汉化批次：25
- Kakera 碎片脚本：53
- Steam 汉化覆盖包：173 个文件
- 翻译、编译及静态验证完成
- D 盘测试副本已完成通关测试
- Steam 覆盖包安装静态验证通过

## 核心技术

- Python 文本处理
- AI 辅助翻译
- 术语表管理
- 游戏脚本编译与验证
- SHA-256 文件完整性校验
- 有固定快照、独立备份和事务日志的保守部署与恢复原语
- Steam 汉化覆盖包制作

## 仓库目标

将一次完整汉化项目中的技术经验，沉淀为可复用的工具、模板和工程规范。

## 版权说明

本仓库不提供游戏原版文件、受保护的游戏脚本、游戏资源或未经授权的第三方工具。

## 许可证

本仓库根目录的 MIT License 适用于 `suzhelu` 有权许可的仓库工具代码及文档。该许可证不授予任何第三方游戏、游戏资源、原版脚本、译文、商标或其他受保护内容的使用权。第三方内容仍受其各自权利人的条款约束。

## 首批可运行 Python 工具

本仓库提供通用文件安全工具、保守的 BGI 脚本文本槽适配器，以及严格白名单部署事务原语：Windows 路径规范化与白名单差异检查、流式 SHA-256、JSON manifest 生成和验证、按显式调用参数规则识别 BGI 文本槽，以及基于固定快照、独立备份、逐文件日志和全目录差异审计的部署与恢复。代码、合成示例及自动化测试位于 `src/`、`examples/` 和 `tests/`。

- 安装与使用：[`docs/python-toolkit-quickstart.md`](docs/python-toolkit-quickstart.md)
- 来源与改造说明：[`docs/code-migration.md`](docs/code-migration.md)
- 测试结果：[`docs/test-report.md`](docs/test-report.md)
- 公开安全检查：[`docs/publication-safety-review.md`](docs/publication-safety-review.md)
- BGI 适配器说明：[`docs/bgi-adapter.md`](docs/bgi-adapter.md)
- 部署事务、崩溃恢复及限制：[`docs/deployment-transactions.md`](docs/deployment-transactions.md)

在仓库根目录运行测试：`python -m unittest discover -s tests -v`。部署事务只作用于调用方提供的现有文件白名单，不负责完整安装器功能，也不会猜测版本或部署目标。BGI 适配器只处理已配置 schema 覆盖的文本调用；未知含字符串调用或动态菜单会被报告并阻止写回。它不是完整 BGI 语言解析器或编译器。仓库不包含游戏文件、真实游戏文本、译文或第三方编译器。
