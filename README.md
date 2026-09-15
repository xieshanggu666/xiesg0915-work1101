# IFC 建筑模型核查工具

读取 IFC 文件中的**墙体、门、窗、房间**，自动完成四类核查并导出报告：

1. **未闭合的墙**
   - 自由墙端：墙端头在容差范围内没有与任何墙体连接；
   - 墙段缺口：两段墙端头相对但没有搭接，量化缺口长度；
   - 房间围护缺口：逐房间检查边界，找出没有被墙 / 门覆盖的开口段。
2. **重复构件**：同类型构件形心重合（默认 80mm）且几何一致（体积比 / 轮廓 IoU），
   通过并查集聚类成组，输出每组的全部 GlobalId。
3. **房间净面积清单**：优先采用 `Qto_SpaceBaseQuantities.NetFloorArea`
   声明值，缺失时由房间平面几何计算；并校验声明值与几何值偏差（默认 >2% 警告），
   统计每个房间的门、窗数量与围护闭合状态。
4. **门窗规格清单（门窗表）**：按**楼层 + 房间**归并统计每类门 / 窗的
   类型、宽×高尺寸与数量；自动标出两类问题门窗——
   - **尺寸异常**：宽 / 高缺失或小于配置下限（默认门 600×1800mm、窗 400×400mm）；
   - **未归属**：没有归到任何房间的门窗（门位于两个房间边界时计入两侧）。
   尺寸优先取 IFC `OverallWidth/OverallHeight` 属性，缺失时按几何包围盒推断。

五组判定阈值（自由端 / 墙段缺口、房间围护缺口、重复构件、面积偏差、
门窗规格）均**可配置**：
内置标准/严格/宽松三套预设，也可用 JSON 配置文件或命令行 `--set` 单项调整，
每次报告（Excel「判定阈值」表、JSON、平面图标注、控制台）都会注明本次使用的阈值方案。

支持**多模型批量核查与项目质量看板**（`batch` 子命令）：一次纳入项目下
多个单体 IFC，按项目 / 单体 / 楼层汇总问题分布、净面积与门窗规格指标，
按批次留存结果形成趋势对比，并由可配置的放行门禁在质量不达标时阻断放行，
详见下文「多模型批量核查与项目质量看板」。

结果支持：

- **点击问题定位构件**：GUI 中点击问题列表，三维视图高亮对应构件并缩放到该位置；
  也可一键在 PyVista 交互窗口中打开（可旋转、缩放、点选）。
- **导出表格**：Excel（汇总 / 问题清单 / 房间净面积 / 重复构件 /
  门窗表 / 门窗明细 6 张表）、CSV、JSON。
- **导出标注图**：平面标注图（问题编号 + 红线标出围护缺口 +
  红 / 紫色虚线圈标尺寸异常 / 未归属门窗）与三维标注图。

## 安装

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# GUI 需要系统 Python 带 tkinter（Debian/Ubuntu：sudo apt install python3-tk）
```

中文字体：项目 `fonts/` 目录可放置 `NotoSansSC.ttf`，程序会自动注册；
否则尝试使用系统已装的中文字体（微软雅黑 / 黑体 / 文泉驿等）。

## 命令行用法

```bash
# 核查并导出全部报告到 output/
python -m ifc_audit.cli audit path/to/model.ifc -o output/

# 切换严格预设（竣工审查）/ 宽松预设（方案阶段粗模）
python -m ifc_audit.cli audit model.ifc --profile strict

# 用配置文件调阈值；命令行再单项覆盖（长度 mm，面积偏差 %）
python -m ifc_audit.cli audit model.ifc --config thresholds.json \
    --set gap_min_len_mm=50 --set area_dev_warn_pct=1

# 生成带中文说明的配置模板（可基于 strict / loose 预设生成）
python -m ifc_audit.cli init-config thresholds.json --profile default

# CI 场景：存在错误级问题时退出码为 1
python -m ifc_audit.cli audit model.ifc --fail-on-error -q

# 图形界面（浏览文件、阈值设置、点击定位、导出）
python -m ifc_audit.cli gui
```

输出文件（以模型名 `sample` 为例）：

| 文件 | 内容 |
| --- | --- |
| `sample_核查报告.xlsx` | 汇总、问题清单、房间净面积、重复构件、门窗表、门窗明细六张表 |
| `sample_问题清单.csv` / `sample_房间净面积.csv` / `sample_门窗表.csv` | 对应表格的 CSV |
| `sample_标注平面图.png` | 平面图：墙/房间/门窗 + 问题编号 + 围护缺口红线 + 异常/未归属门窗圈标 |
| `sample_三维标注.png` | 三维轴测标注图（无 GPU 环境自动用 matplotlib 渲染） |
| `sample_结果.json` | 机器可读的完整结果（含门窗明细与门窗表） |

## 多模型批量核查与项目质量看板

一次纳入一个项目的**多个单体 IFC**（文件 / 目录 / 多路径混传均可），
按 **项目 → 单体 → 楼层** 三级汇总问题分布、净面积与门窗规格指标；
每批结果按项目**留存快照**，自动与上一批次形成趋势对比；
放行规则（质量门禁）不达标时以**退出码 3 阻断放行**，可直接挂 CI。

```bash
# 批量核查目录下全部单体（自动发现 .ifc/.ifcxml/.ifczip）
python -m ifc_audit.cli batch path/to/ifc目录/ --project 花园小区一期 \
    --label "v1首次提模" -o output/batch/

# 多个文件 / 目录混传
python -m ifc_audit.cli batch 1号楼.ifc 2号楼.ifc 地库/ --project XX项目

# 竣工审查用严格门禁；方案阶段用宽松门禁；--no-gate 只统计不阻断
python -m ifc_audit.cli batch ifc目录/ --project XX --gate-profile strict
python -m ifc_audit.cli batch ifc目录/ --project XX --no-gate

# 生成带中文说明的门禁配置模板，单项放行规则也可 --gate-set 覆盖
python -m ifc_audit.cli init-gate gate.json
python -m ifc_audit.cli batch ifc目录/ --project XX \
    --gate-config gate.json --gate-set unit_max_warnings=20

# 不导出每个单体的 Excel/平面图（只出批次报告，批量更快）
python -m ifc_audit.cli batch ifc目录/ --project XX --no-unit-reports

# 查看项目历次批次趋势（控制台），-o 同时导出趋势图
python -m ifc_audit.cli trend --project 花园小区一期 -o output/trend.png
```

退出码：`0`=核查完成且门禁通过（准予放行）；`2`=配置 / 参数错误；
`3`=**门禁不达标，已阻断放行**；核查阈值沿用 `--profile/--config/--set`。

> 不同目录下的**同名 IFC 会自动消歧**：单体名带上直接父目录
> （如 `A区/楼A.ifc`、`B区/楼A.ifc` → 单体 `A区-楼A`、`B区-楼A`），
> 父目录也相同则继续向上一级，保证批次内单体名、门禁判定、看板楼层行与
> 导出的单体报告文件名各自独立，不会串行结论或互相覆盖；单体的完整文件路径
> 仍记录在 Excel「单体汇总」与批次 JSON 中。

### 输出文件

| 文件 | 内容 |
| --- | --- |
| `<项目>_批次核查报告_<批次号>.xlsx` | 8 张表：批次概览、**放行判定**、单体汇总、楼层汇总、问题分布（单体×问题类型）、门窗规格汇总（跨单体门窗表）、趋势对比、阈值与门禁 |
| `<项目>_质量看板_<批次号>.png` | 项目质量看板：KPI 卡片、各单体问题堆叠、问题类型分布、净面积/不闭合房间、楼层问题、门窗异常/未归属、门窗规格构成、批次趋势与门禁未通过项清单 |
| `<项目>_批次结果_<批次号>.json` | 机器可读的完整批次结果（含门禁逐条判定与趋势增量） |
| `单体报告/<单体>_*` | 每个成功核查单体的单模型 Excel / CSV / 平面标注图（`--with-3d` 含三维图），格式与 `audit` 子命令一致 |
| `batch_history/<项目>/batches/*.json` | 按项目归档的**批次快照**，供后续批次趋势对比（`--history` 可改目录） |

### 放行门禁规则

门禁分两级逐规则判定，全部通过才放行（结果写入 Excel「放行判定」表与看板）：

- **单体级**（任一单体不达标即阻断）：错误数、警告数、每千 m² 错误数、
  围护不闭合房间数与占比、未归属门窗数、尺寸异常门窗数与占比、重复构件组数；
- **项目级**：错误总数、重复构件组总数、净面积为 0 房间数、
  最少纳入单体数（`min_units`，防止漏传文件）；
- **批次完整性**：存在无法解析的 IFC 默认直接阻断（`allow_failed_files=true` 可放开）。

| 门禁预设 | 适用场景 | 单体错误 | 不闭合房间占比 | 异常门窗占比 | 重复构件组 |
| --- | --- | --- | --- | --- | --- |
| `default` 标准 | 施工图模型常规放行 | 0 | 5% | 5% | 0 |
| `strict` 严格 | 竣工审查 | 0 | 2% | 2% | 0（警告数也受限） |
| `loose` 宽松 | 方案阶段粗模 | ≤5 | 10% | 10% | ≤3 |
| `none` 不设门禁 | 仅统计体检 | 不限制 | 不限制 | 不限制 | 不限制 |

上限类规则取 `-1` 即关闭该条；占比字段单位为 `%`，布尔字段取 `true/false`。

### 批次趋势

每次 `batch` 都会把精简快照留存到 `<history>/<项目名>/batches/`，
下次同项目核查时自动取上一批次对比：八项核心指标（问题总数 / 错误 / 警告 /
重复构件组 / 净面积 / 不闭合房间 / 未归属门窗 / 尺寸异常门窗）的增减量、
单体级错误变化、新增与缺失单体；看板趋势图按全部历史批次绘制，
阻断批次红底标注 `BLOCKED`。

## 图形界面

`python -m ifc_audit.cli gui` 打开窗口：

- 顶部选择 IFC 文件并执行核查；
- 左侧「问题清单」按严重程度着色（红=错误 / 黄=警告 / 蓝=提示），
  点击任意一条，右侧三维视图高亮关联构件并缩放定位，下方显示详情与 GlobalId；
- 「房间净面积」标签页给出每个房间的面积、来源、门窗数与闭合状态，
  点击可在三维中定位房间；
- 「门窗表」标签页按楼层 / 房间 / 类型 / 宽×高 汇总数量，
  橙底行为尺寸异常、灰底行为未归属房间的门窗，点击可在三维中定位；
- 「在 PyVista 中打开」启动独立的 PyVista 交互窗口（与 Tk 主循环隔离，
  避免 VTK/Tk 冲突），可旋转缩放查看；
- 「导出到目录…」保存全部表格与标注图。

## 作为 Python 库调用

```python
from ifc_audit.pipeline import audit_ifc
from ifc_audit import report

model = audit_ifc("model.ifc")
print(model.summary())
for issue in model.issues:
    print(issue.issue_id, issue.title, issue.global_ids)
for room in model.rooms:
    print(room.name, room.net_area, "m²", "闭合" if room.enclosed else "有缺口")
for row in model.opening_schedule:
    print(row.storey, row.room_name, "门" if row.kind == "door" else "窗",
          row.type_name, row.width, "×", row.height, "×", row.count,
          row.notes)

report.export_excel(model, "out/报告.xlsx")
report.export_annotated_plan(model, "out/标注图.png")
```

批量核查 / 看板 / 门禁（Python API）：

```python
from ifc_audit.batch import run_batch_with_config, attach_trend, save_batch_snapshot
from ifc_audit import batch_report

batch = run_batch_with_config(
    ["ifc/1号楼.ifc", "ifc/2号楼.ifc"],
    project="花园小区一期", label="v1提模",
    threshold_profile="default",          # 核查阈值：与 audit 一致
    gate_profile="default",               # 放行门禁
    gate_overrides={"unit_max_warnings": 20})

for u in batch.units:                     # 单体汇总
    print(u.name, u.errors, "错误", u.total_net_area, "m²",
          "不闭合房间", u.rooms_open, "异常门窗", u.opening_anomaly)
for s in batch.storeys:                   # 单体×楼层汇总
    print(s.unit, s.storey, s.net_area, s.errors)

print(batch.gate_passed)                  # True=放行，False=阻断
for r in batch.gate_results:              # 逐条门禁判定
    if not r.passed:
        print("[阻断]", r.scope, r.message)

attach_trend(batch, "output/batch_history")   # 附加与上一批次的趋势对比
save_batch_snapshot(batch, "output/batch_history")
batch_report.export_batch_excel(batch, "out/批次报告.xlsx")
batch_report.export_dashboard(batch, "out/质量看板.png")
batch_report.export_batch_json(batch, "out/批次结果.json")
```

三维定位（桌面环境）：

```python
from ifc_audit.viewer import Viewer3D

viewer = Viewer3D(model)
viewer.locate(issue.global_ids)   # 高亮并把相机对准构件
viewer.run()
```

## 判定阈值配置

五组判定的全部阈值集中在 `ifc_audit/thresholds.py`，调整方式有三种，
**优先级从低到高**：内置预设 → 配置文件 → 命令行单项覆盖。

### 1. 内置预设

| 预设 | 适用场景 | 自由端容差 | 缺口聚类 | 围护缺口下限 | 重复形心距 | 体积比 / IoU | 面积偏差 | 门窗尺寸下限 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `default` 标准 | 常规施工图模型 | 50mm | 300mm | 100mm | 80mm | 0.85 / 0.70 | 2% | 门 600×1800，窗 400×400 |
| `strict` 严格 | 高精度模型 / 竣工审查 | 30mm | 200mm | 50mm | 50mm | 0.90 / 0.80 | 1% | 门 700×2000，窗 500×500 |
| `loose` 宽松 | 方案阶段 / 粗模 | 100mm | 500mm | 200mm | 150mm | 0.80 / 0.60 | 5% | 门 500×1500，窗 300×300 |

### 2. JSON 配置文件

```bash
python -m ifc_audit.cli init-config thresholds.json   # 生成带中文说明的模板
python -m ifc_audit.cli audit model.ifc --config thresholds.json
```

文件中 `profile` 指定基准预设，其余键逐项覆盖；以 `_` 开头的键为注释，
会被忽略。用户面单位：长度 **毫米（mm）**，面积偏差 **百分比（%）**，
体积比 / IoU 为 0~1 小数：

```json
{
  "profile": "default",
  "gap_min_len_mm": 50,
  "area_dev_warn_pct": 1,
  "dup_iou": 0.80
}
```

### 3. 命令行单项覆盖

`--set key=value` 可重复，优先级最高；非法键名或越界值会报错并以退出码 2 终止：

```bash
python -m ifc_audit.cli audit model.ifc \
    --profile strict --set dup_iou=0.95 --set free_end_tol_mm=20
```

### 可配置键一览

| 配置键 | 含义 | 单位 | 分组 |
| --- | --- | --- | --- |
| `free_end_tol_mm` | 墙端头伸入其它墙体的判定容差 | mm | 自由端 |
| `endpoint_merge_tol_mm` | 邻近自由端聚类为「墙段缺口」的容差 | mm | 墙段缺口 |
| `gap_min_len_mm` | 房间围护缺口最小上报长度 | mm | 围护缺口 |
| `barrier_buffer_mm` | 围护覆盖外扩容差（毫米级建模误差吸收） | mm | 围护缺口 |
| `dup_centroid_tol_mm` | 重复构件形心距离 | mm | 重复构件 |
| `dup_vol_ratio` | 重复构件体积相似度（小/大） | 0~1 | 重复构件 |
| `dup_iou` | 重复构件平面轮廓 IoU | 0~1 | 重复构件 |
| `area_dev_warn_pct` | 声明面积与几何面积偏差警告线 | % | 面积偏差 |
| `door_min_width_mm` | 门最小宽度（小于即标尺寸异常） | mm | 门窗规格 |
| `door_min_height_mm` | 门最小高度 | mm | 门窗规格 |
| `win_min_width_mm` | 窗最小宽度 | mm | 门窗规格 |
| `win_min_height_mm` | 窗最小高度 | mm | 门窗规格 |

### 报告中注明本次阈值

- Excel 汇总表新增「判定阈值方案」行，并新增 **「判定阈值」工作表**
  （分组 / 判定项 / 本次取值 / 配置键）；
- `*_结果.json` 的 `thresholds` 段记录完整取值与来源（预设名、配置文件路径、覆盖项）；
- Excel **「门窗表 / 门窗明细」**工作表与 `*_门窗表.csv`、JSON `openings`
  段给出按楼层 / 房间归并的门窗规格统计，异常行橙底、未归属行灰底；
- 平面标注图页脚、控制台汇总均打印阈值方案说明；
- 每条问题详情使用**本次实际阈值**描述（如「上报下限 50mm」「偏差超过警告线 1%」）。

作为 Python 库调用时，可直接传阈值对象（内部单位为米/比例）：

```python
from ifc_audit.pipeline import audit_ifc_with_config, audit_ifc
from ifc_audit.thresholds import resolve

# 方式一：与 CLI 一致的解析（预设 + 配置文件 + 用户单位覆盖）
model = audit_ifc_with_config("model.ifc",
                              profile="strict",
                              config_path="thresholds.json",
                              overrides={"gap_min_len_mm": 50})

# 方式二：自行解析后传入
th, prov = resolve("default", overrides={"free_end_tol_mm": 20})
model = audit_ifc("model.ifc", thresholds=th, provenance=prov)
```

GUI 中点击工具栏「阈值设置…」可切换预设、载入配置文件或逐项编辑，
下次「开始核查」生效。

## 核查方法说明

- **几何提取**：优先解析参数化 `IfcExtrudedAreaSolid` 的二维轮廓
  （矩形 / 任意闭合曲线 / 圆形，支持挤出方向与 RefDirection），
  得到轴对齐的精确平面轮廓，避免 BRep 三角化在顶盖附近产生斜面瑕疵；
  BRep / 曲面等表示回退到「21 个高度水平切片 + shapely polygonize」。
- **墙体围护**：取门楣上方的完整墙身截面（参数化轮廓或最大面积切片），
  与门扇凸包联合后，对每个房间的平面边界做 `difference`；
  未被覆盖且长度超过 100mm 的线段即围护缺口。窗不参与围护（窗台以上为采光面）。
- **自由端 / 缺口**：用 PCA 从墙轮廓求中轴线与端点，
  检查端点是否进入其它墙体 50mm 范围；再把邻近的自由端聚类，
  同一位置出现 ≥2 个不同墙的端头时判为「墙段缺口」，否则为「自由墙端」。
- **重复构件**：同类构件两两比较，形心距 < 80mm、体积比 ≥ 0.85、
  封闭轮廓 IoU ≥ 0.70 即判重，并查集聚类成组。
- **门窗表**：门 / 窗宽高优先取 IFC `OverallWidth/OverallHeight`
  （单位随项目长度单位换算为米），属性缺失时取几何包围盒——
  水平长边为洞口宽度、竖直方向为高度；类型优先取 `ObjectType`，
  其次映射 `PredefinedType`（平开门 / 推拉门 / 天窗等），缺省为“门 / 窗”。
  按 楼层 + 归属房间 + 类型 + 宽×高 归并计数；归属规则与房间净面积清单
  一致（形心落在房间内或距边界 ≤ 归属距离），跨房间门计入两侧，
  没有任何房间接收的门窗单独列为「（未归属房间）」。

判定阈值集中在 `ifc_audit/thresholds.py`，支持内置预设（标准/严格/宽松）、
JSON 配置文件与命令行 `--set` 三种方式调整，详见上文「判定阈值配置」。
每次报告都会注明本次使用的阈值方案。

## 生成自带已知问题的样例模型

```bash
python tools/make_sample_ifc.py output/sample.ifc
python -m ifc_audit.cli audit output/sample.ifc -o output/
```

样例中人为注入：1 组重复墙、1 组重复门、1 个室内自由墙垛（2 个自由端）、
1 处 150mm 墙段缺口与对应房间围护缺口、1 个面积偏差房间、1 个无声明面积房间、
1 樘 300mm 宽异常小窗、1 樘游离门与 1 樘游离窗（未归属任何房间）。

可用多个样例 IFC 体验批量核查与门禁阻断（会退出码 3）：

```bash
python tools/make_sample_ifc.py /tmp/proj/1号楼.ifc
python tools/make_sample_ifc.py /tmp/proj/2号楼.ifc
python -m ifc_audit.cli batch /tmp/proj --project 示例项目
```

## 模块结构

```
ifc_audit/
  units.py       单位换算（项目长度单位 -> 米）
  geometry.py    网格切片、轮廓提取、中轴线/厚度
  analytic.py    参数化 IfcExtrudedAreaSolid 轮廓解析
  extract.py     IFC 提取墙/门/窗/房间
  thresholds.py  判定阈值：预设 / 配置文件 / 命令行覆盖
  gate.py        放行门禁：规则 / 预设 / 配置解析 / 模板导出
  checks.py      重复构件、自由端、墙段缺口、房间围护
  rooms.py       房间净面积清单
  openings.py    门窗规格清单（门窗表 / 异常 / 未归属）
  batch.py       多模型批量核查、项目/单体/楼层聚合、门禁判定、批次快照与趋势
  batch_report.py 批次 Excel / 项目质量看板 PNG / 批次 JSON / 趋势图
  report.py      Excel / CSV / 平面标注图
  viewer.py      PyVista 三维查看器 + matplotlib 三维回退
  viewer_win.py  独立进程 PyVista 窗口
  gui.py         Tkinter 图形界面
  cli.py         命令行入口（audit / batch / trend / init-gate / gui）
  pipeline.py    流程编排
tools/
  make_sample_ifc.py  样例模型生成
```

## 已知限制

- 核查在二维水平面进行，适用于常规正交墙体；斜墙（挤出方向带水平分量）
  在参数化解析中按 RefDirection 处理，曲面墙的缺口检测精度有限。
- 无 GPU / 无显示的服务器上 PyVista 交互窗口与离屏渲染不可用，
  会自动改用 matplotlib 三维图；表格与平面标注图不受影响。
- 房间-门窗归属按平面距离关联，门位于两个房间边界时会计入两侧。
