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
- PowerShell 安全部署与回滚
- Steam 汉化覆盖包制作

## 仓库目标

将一次完整汉化项目中的技术经验，沉淀为可复用的工具、模板和工程规范。

## 版权说明

本仓库不提供游戏原版文件、受保护的游戏脚本、游戏资源或未经授权的第三方工具。

## 首批可运行 Python 工具

本仓库新增一组通用文件安全工具：Windows 路径规范化与白名单差异检查、流式 SHA-256、JSON manifest 生成和验证。代码、合成示例及自动化测试位于 `src/`、`examples/` 和 `tests/`。

- 安装与使用：[`docs/python-toolkit-quickstart.md`](docs/python-toolkit-quickstart.md)
- 来源与改造说明：[`docs/code-migration.md`](docs/code-migration.md)
- 测试结果：[`docs/test-report.md`](docs/test-report.md)
- 公开安全检查：[`docs/publication-safety-review.md`](docs/publication-safety-review.md)

在仓库根目录运行测试：`python -m unittest discover -s tests -v`。当前工具不包含游戏解析器、编译器、部署器、游戏文件或汉化文本。
