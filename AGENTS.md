# 项目维护约定

这是中文研究资料与 dage-skill 仓库，不是行情服务。维护流程见 [CONTRIBUTING.md](CONTRIBUTING.md)。

- 资料覆盖范围与核验状态以 [docs/catalog.json](docs/catalog.json) 为准；不得因编辑文件而刷新事实日期。
- 已核实纠错和待查问题见 [docs/FACT_CHECKS.md](docs/FACT_CHECKS.md)。未知事实标为待核实，不补造引用。
- 保持既有中文文件名和链接兼容；根目录新增研究资料需登记台账。
- 改动后运行 `python3 scripts/check.py`；修改检查脚本时运行 `python3 -m unittest discover -s tests -v`。
- 检查脚本使用 Python 3.10+ 标准库，保持离线可运行。
