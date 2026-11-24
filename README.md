# 自行车架遮挡条件的识别覆盖与功能适用范围

本地合成数据应用：持续导入车辆、车架登记、行程、后方目标复核与试用范围资料，
在 **V15 携带自行车架使用辅助驾驶** 的场景下，给出可解释的车型配置适用性评估，
并维护有限试用名单。所有车辆、地区、事件标识均为虚构值，不包含真实驾驶员、
车辆或道路资料；新闻报道中的厂商发布信息不是本资料集的实测结论。

## 业务口径（关键）

1. **行程保留当时生效的配置**：车架拆装时间可能晚于车辆登记，按行程时刻落在
   哪些登记区间判定；事后更正不回改历史行程，只触发待执行名单重新核验。
2. **有车架 ≠ 遮挡摄像头**：是否遮挡以行程的 `rear_visibility` 字段为准。
3. **检测到物体 ≠ 正确理解车尾外形**：只有人工复核 `confirmed` 且 `detected=yes`
   的后摄记录才是确认识别；复核 `uncertain`/`revised` 各自单列。
4. **没有报告目标 ≠ 识别成功**：无后摄记录记为「未观测」，资料缺失或完全遮挡
   记为「无法评估」，均不计入正向证据。
5. **证据六分类**：确认识别 / 未识别 / 复核不确定 / 复核已更正 / 未观测 / 无法评估。
6. 真实驾驶员接管（`intervention_id` 为非空且非占位 `E-00`）是否决性硬红线；
   仅 V15 行程构成本功能证据，V14 只作历史基线。

## 环境与测试

Python 3.11 至 3.14，无第三方依赖。建议使用解释器隔离模式运行，避免不可信数据
目录中的同名模块被导入：

```sh
python3 -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
python3 -I -m unittest discover -s tests -v
python3 -I tools/check_materials.py
```

`data/` 中的 CSV 使用 UTF-8 BOM 编码，首行是字段名，契约见
[`docs/资料契约.md`](docs/资料契约.md)。

## 命令行用法

所有命令通过 `python -I -m bicycle_rack_visibility_scope.cli ...` 调用
（未安装包时可先 `pip install -e .`，或设置 `PYTHONPATH=src` 后去掉 `-I`）。

```sh
# 1. 持续导入：整套 CSV 或单表 CSV，按主键幂等 upsert，记录导入批次
python -I -m bicycle_rack_visibility_scope.cli import --data-dir data --store .store
python -I -m bicycle_rack_visibility_scope.cli import --store .store \
    --table racks --file data/rack_installations.csv

# 2. 适用性评估（中文文本报告 + JSON/CSV 机器可读输出，门槛可显式调整）
python -I -m bicycle_rack_visibility_scope.cli evaluate --store .store \
    --report-out reports --min-trips 3 --min-positive 2

# 3. 试用名单核验：给出建议状态，不直接改写名单（需负责人审批）
python -I -m bicycle_rack_visibility_scope.cli reconcile --store .store

# 4. 决定溯源：某条范围引用了哪些行程级条件
python -I -m bicycle_rack_visibility_scope.cli trace --store .store --scope-id TS-03

# 5. 覆盖缺口：哪些车型配置缺视野/载荷样本
python -I -m bicycle_rack_visibility_scope.cli gaps --store .store

# 6. 车架配置更正影响：旧值需用更正前 CSV 保留
python -I -m bicycle_rack_visibility_scope.cli impact-rack --store .store \
    --installation-id RI-002 --old-file /path/to/old_rack_installations.csv

# 7. 新复核批次影响：哪些待执行对象需要重核
python -I -m bicycle_rack_visibility_scope.cli impact-review --store .store --batch RB-2
```

## 评估结论与名单状态

| 评估结论 | 含义 | 名单建议 |
| --- | --- | --- |
| `eligible_trial` | 满足样本量、确认识别数、视野与载荷覆盖门槛，无硬红线 | `partial` 受限试用 |
| `needs_samples` | 无安全红线但样本/覆盖不足，未观测只披露不计成功 | `proposed` 继续拟建 |
| `blocked` | 存在真实接管、确认未识别、复核更正或配置未确认 | `hold` 暂停；已接受范围也降级复核 |

名单 `accepted` 记录不会被自动重核或改写；`proposed`/`partial` 在车架更正或
新复核批次导入后进入重核清单，由评估负责人审批。

## 独立合成测试数据

`tools/generate_data.py` 生成与基线资料独立的固定场景（含登记冲突、待复核/已
更正登记、遮挡、资料缺失、未观测、占位与真实接管等边界）：

```sh
python3 -I tools/generate_data.py --out /tmp/fixture
```

## 模块结构

```
src/bicycle_rack_visibility_scope/
  domain.py       领域模型、取值字典、证据六分类
  loader.py       CSV 严格校验导入（枚举/时间/引用完整性，错误带行号）
  store.py        JSONL 本地仓库：幂等 upsert、导入批次溯源
  timeline.py     行程时点车架状态还原、行程证据分级
  evaluation.py   车型配置维度评估、政策门槛、名单核验
  governance.py   决定溯源、覆盖缺口、车架更正/复核批次影响分析
  reports.py      中文文本与 JSON/CSV 报告
  cli.py          命令行入口
```

设计细节见 [`docs/应用设计.md`](docs/应用设计.md)。
