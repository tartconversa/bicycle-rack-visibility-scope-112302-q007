"""自行车架遮挡条件的识别覆盖与功能适用范围评估。

模块：
- domain: 领域模型、取值字典与证据分级
- loader: 合成 CSV 的严格校验导入
- store: 持续导入的本地 JSONL 仓库（幂等 upsert + 批次溯源）
- timeline: 行程时点车架状态还原与行程证据分级
- evaluation: 车型配置维度的适用性评估与名单核验
- governance: 决定溯源、覆盖缺口、更正/复核影响分析
- reports: 中文文本与 JSON/CSV 报告
- cli: 命令行入口
"""

__version__ = "0.1.0"
