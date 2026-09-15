"""命令行入口。

用法::

    python -m ifc_audit.cli audit model.ifc -o output/
    python -m ifc_audit.cli batch ifc目录/ --project XX项目 -o output/batch/
    python -m ifc_audit.cli trend --project XX项目
    python -m ifc_audit.cli gui              # 图形界面
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

from .pipeline import audit_ifc, audit_ifc_with_config
from . import report
from .report import KIND_CN, SEV_CN
from .thresholds import (
    PROFILES, PROFILE_CN, parse_set_items, ThresholdConfigError,
    write_config_template,
)
from .gate import (
    GATE_PROFILES, GATE_PROFILE_CN, parse_gate_set_items, GateConfigError,
    write_gate_config_template,
)
from .batch import (
    run_batch_with_config, attach_trend, save_batch_snapshot,
    load_project_history,
)
from . import batch_report


def _cmd_audit(args) -> int:
    out_dir = args.output
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(args.ifc))[0]

    def progress(pct, msg):
        if not args.quiet:
            print(f"[{pct:3d}%] {msg}", flush=True)

    try:
        overrides = parse_set_items(args.set_threshold)
        model = audit_ifc_with_config(
            args.ifc, progress=progress if not args.quiet else None,
            profile=args.profile, config_path=args.config,
            overrides=overrides or None)
    except ThresholdConfigError as exc:
        print(f"阈值配置错误：{exc}", file=sys.stderr)
        return 2
    s = model.summary()

    print("\n================ 核查汇总 ================")
    print(f"文件        : {s['file']}")
    print(f"墙体/门/窗  : {s['walls']} / {s['doors']} / {s['windows']}")
    print(f"房间        : {s['rooms']}    净面积合计: {s['total_net_area']} m²")
    print(f"问题        : {s['issues']} 条 (错误 {s['errors']} / 警告 {s['warnings']})")
    print(f"重复构件组  : {s['duplicate_groups']}")
    print(f"阈值方案    : {model.threshold_provenance.describe()}")

    if model.issues:
        print("\n---------------- 问题清单 ----------------")
        for n, i in enumerate(model.issues, start=1):
            print(f"{n:>3}. {i.issue_id} [{SEV_CN.get(i.severity, i.severity)}] "
                  f"{KIND_CN.get(i.kind, i.kind)} | {i.title}"
                  f"{f'  ({i.storey})' if i.storey else ''}")

    print("\n---------------- 房间净面积 --------------")
    print(f"{'房间':<14}{'楼层':<10}{'净面积m²':>10}{'来源':>8}"
          f"{'门':>4}{'窗':>4}  围护状态")
    for r in model.rooms:
        print(f"{(r.name or '')[:14]:<14}{(r.storey or '')[:10]:<10}"
              f"{r.net_area:>10.2f}{('声明' if r.area_source == 'declared' else '几何'):>8}"
              f"{r.doors:>4}{r.windows:>4}  {r.enclosure_label}")

    print("\n---------------- 门窗表 ------------------")
    from .openings import size_label
    print(f"{'楼层':<8}{'房间':<14}{'类':<4}{'类型':<10}"
          f"{'规格(mm)':<12}{'数量':>4}  备注")
    for r in model.opening_schedule:
        print(f"{(r.storey or '-')[:8]:<8}{r.room_name[:14]:<14}"
              f"{('门' if r.kind == 'door' else '窗'):<4}"
              f"{(r.type_name or '')[:10]:<10}{size_label(r.width, r.height):<12}"
              f"{r.count:>4}  {r.notes}")

    outputs = {}
    xlsx = os.path.join(out_dir, f"{base}_核查报告.xlsx")
    report.export_excel(model, xlsx)
    outputs["excel"] = xlsx

    report.export_issues_csv(model, os.path.join(out_dir, f"{base}_问题清单.csv"))
    report.export_rooms_csv(model, os.path.join(out_dir, f"{base}_房间净面积.csv"))
    outputs["issues_csv"] = os.path.join(out_dir, f"{base}_问题清单.csv")
    outputs["rooms_csv"] = os.path.join(out_dir, f"{base}_房间净面积.csv")

    report.export_openings_csv(model, os.path.join(out_dir, f"{base}_门窗表.csv"))
    outputs["openings_csv"] = os.path.join(out_dir, f"{base}_门窗表.csv")

    plan = os.path.join(out_dir, f"{base}_标注平面图.png")
    report.export_annotated_plan(model, plan)
    outputs["annotated_plan"] = plan

    # 三维图：优先 pyvista 离屏渲染；无 GL/显示环境自动降级 matplotlib
    view3d = os.path.join(out_dir, f"{base}_三维标注.png")
    try:
        from .viewer import Viewer3D, offscreen_render_available, matplotlib_screenshot
        if offscreen_render_available():
            Viewer3D(model).screenshot(view3d)
        else:
            if not args.quiet:
                print("[info] 当前环境无 GPU/显示，三维图改用 matplotlib 渲染；"
                      "在桌面环境运行 `python -m ifc_audit.cli gui` 可使用 PyVista 交互定位。")
            matplotlib_screenshot(model, view3d)
    except Exception as exc:
        if not args.quiet:
            print(f"[warn] 三维渲染失败（{exc}），改用 matplotlib。")
        from .viewer import matplotlib_screenshot
        matplotlib_screenshot(model, view3d)
    outputs["view3d"] = view3d

    # 机器可读 JSON（GUI / 后续流水线使用）
    from dataclasses import asdict
    dump = {
        "summary": s,
        "thresholds": {
            "values": asdict(model.thresholds) if model.thresholds else None,
            "provenance": (model.threshold_provenance.to_dict()
                           if model.threshold_provenance else None),
        },
        "issues": [
            {
                "id": i.issue_id, "severity": i.severity, "kind": i.kind,
                "title": i.title, "detail": i.detail,
                "global_ids": i.global_ids,
                "location": list(i.location), "storey": i.storey,
                "measure": i.measure,
            } for i in model.issues
        ],
        "rooms": [vars(r) for r in model.rooms],
        "openings": {
            "items": [vars(o) for o in model.opening_items],
            "schedule": [vars(r) for r in model.opening_schedule],
        },
        "outputs": outputs,
    }
    json_path = os.path.join(out_dir, f"{base}_结果.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(dump, f, ensure_ascii=False, indent=2, default=str)

    print("\n---------------- 导出文件 ----------------")
    for k, v in outputs.items():
        print(f"{k:<16}: {v}")
    print(f"{'json':<16}: {json_path}")

    return 1 if (s["errors"] > 0 and args.fail_on_error) else 0


def _print_batch_summary(batch) -> None:
    t = batch.totals
    print("\n============== 项目批量核查汇总 ==============")
    print(f"项目        : {batch.project}")
    print(f"批次        : {batch.batch_id} {batch.label or ''}（{batch.created_at}）")
    print(f"纳入单体    : {t['units']} 个（成功 {t['units'] - t['units_failed']}"
          f" / 失败 {t['units_failed']}）")
    print(f"构件(墙门窗房间): {t['walls']} / {t['doors']} / "
          f"{t['windows']} / {t['rooms']}")
    print(f"问题        : {t['issues']} 条 (错误 {t['errors']} / "
          f"警告 {t['warnings']} / 提示 {t['infos']})")
    print(f"重复构件组  : {t['duplicate_groups']}")
    print(f"净面积合计  : {t['total_net_area']} m2；不闭合房间 {t['rooms_open']} 间")
    print(f"门窗        : 共 {t['opening_total']} 樘，"
          f"尺寸异常 {t['opening_anomaly']}，未归属 {t['opening_unassigned']}")
    print(f"核查阈值    : {next((u.threshold_describe for u in batch.units if u.ok), '-')}")
    print(f"门禁方案    : {batch.gate.get('description') or '-'}")

    print("\n---------------- 单体汇总 ----------------")
    print(f"{'单体':<16}{'状态':<6}{'错误':>5}{'警告':>5}"
          f"{'重复组':>7}{'净面积m²':>11}{'不闭合':>7}{'异常门窗':>8}")
    for u in batch.units:
        if not u.ok:
            print(f"{u.name[:16]:<16}{'失败':<6}  {u.error}")
            continue
        print(f"{u.name[:16]:<16}{'成功':<6}{u.errors:>5}{u.warnings:>5}"
              f"{u.dup_groups:>7}{u.total_net_area:>11.2f}"
              f"{u.rooms_open:>7}{u.opening_anomaly:>8}")

    fails = [r for r in batch.gate_results if not r.passed]
    if fails:
        print("\n---------------- 门禁未通过项 ----------------")
        level_cn = {"unit": "单体", "project": "项目", "batch": "批次"}
        for r in fails:
            print(f"✗ [{level_cn.get(r.level, r.level)}] {r.scope}：{r.message}")

    tr = batch.trend or {}
    if tr.get("has_previous"):
        print("\n---------------- 趋势对比（相对上一批次）----------------")
        print(f"上一批次：{tr.get('previous_batch_id')} "
              f"{tr.get('previous_label') or ''}（{tr.get('previous_created_at')}）")
        for key, d in tr.get("deltas", {}).items():
            delta = d["delta"]
            if delta == 0:
                arrow = "持平"
            else:
                arrow = f"{'增加' if delta > 0 else '减少'} {abs(delta):g}"
            print(f"  {d['label']:<12} {d['old']:g} → {d['new']:g}  （{arrow}）")
        if tr.get("units_new") or tr.get("units_missing"):
            print(f"  新增单体：{', '.join(tr['units_new']) or '无'}；"
                  f"本批缺失：{', '.join(tr['units_missing']) or '无'}")

    print("\n放行结论：" + ("✅ 准予放行" if batch.gate_passed
                           else "⛔ 不予放行（质量门禁阻断）"))


def _safe_unit_filename(name: str) -> str:
    """单体名 -> 可安全作为文件名的字符串（去掉路径分隔符等非法字符）。"""
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip("_") or "unit"


def _export_unit_reports(batch, out_dir, quiet, with_3d) -> None:
    """为每个成功核查的单体导出单模型 Excel/CSV/平面图（可选三维图）。"""
    sub = os.path.join(out_dir, "单体报告")
    os.makedirs(sub, exist_ok=True)
    used_names: set[str] = set()
    for u in batch.units:
        if u.model is None:
            continue
        # 不同目录同名单体已在批量入口消歧；这里再做一次文件名防御，绝不覆盖
        base = _safe_unit_filename(u.name)
        if base in used_names:
            i = 2
            while f"{base}-{i}" in used_names:
                i += 1
            base = f"{base}-{i}"
        used_names.add(base)
        report.export_excel(u.model, os.path.join(sub, f"{base}_核查报告.xlsx"))
        report.export_issues_csv(u.model, os.path.join(sub, f"{base}_问题清单.csv"))
        report.export_rooms_csv(u.model, os.path.join(sub, f"{base}_房间净面积.csv"))
        report.export_openings_csv(u.model, os.path.join(sub, f"{base}_门窗表.csv"))
        report.export_annotated_plan(
            u.model, os.path.join(sub, f"{base}_标注平面图.png"))
        if with_3d:
            view3d = os.path.join(sub, f"{base}_三维标注.png")
            try:
                from .viewer import (
                    Viewer3D, offscreen_render_available, matplotlib_screenshot)
                if offscreen_render_available():
                    Viewer3D(u.model).screenshot(view3d)
                else:
                    matplotlib_screenshot(u.model, view3d)
            except Exception as exc:
                if not quiet:
                    print(f"[warn] {u.name} 三维渲染失败（{exc}），跳过。")
        if not quiet:
            print(f"  已导出单体报告：{base}")


def _cmd_batch(args) -> int:
    out_dir = args.output
    os.makedirs(out_dir, exist_ok=True)
    history_dir = args.history or os.path.join("output", "batch_history")

    def progress(pct, msg):
        if not args.quiet:
            print(f"[{pct:3d}%] {msg}", flush=True)

    try:
        overrides = parse_set_items(args.set_threshold)
        gate_overrides = parse_gate_set_items(args.gate_set)
        batch = run_batch_with_config(
            args.paths,
            project=args.project,
            label=args.label or "",
            threshold_profile=args.profile,
            threshold_config=args.config,
            threshold_overrides=overrides or None,
            gate_profile=("none" if args.no_gate else args.gate_profile),
            gate_config=(None if args.no_gate else args.gate_config),
            gate_overrides=(None if args.no_gate else (gate_overrides or None)),
            progress=progress if not args.quiet else None)
        attach_trend(batch, history_dir)
    except (ThresholdConfigError, GateConfigError) as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"批量核查失败：{exc}", file=sys.stderr)
        return 2

    if not args.quiet:
        _print_batch_summary(batch)

    # 导出：批次 Excel / 看板 PNG / JSON + 单体报告
    xlsx = batch_report.export_batch_excel(
        batch, os.path.join(out_dir, f"{args.project}_批次核查报告_{batch.batch_id}.xlsx"))
    png = batch_report.export_dashboard(
        batch, os.path.join(out_dir, f"{args.project}_质量看板_{batch.batch_id}.png"))
    jpath = batch_report.export_batch_json(
        batch, os.path.join(out_dir, f"{args.project}_批次结果_{batch.batch_id}.json"))

    if not args.no_unit_reports:
        if not args.quiet:
            print("\n---------------- 导出单体报告 ----------------")
        _export_unit_reports(batch, out_dir, args.quiet, args.with_3d)

    # 快照留存（批次 JSON 导出后再留存，供后续批次趋势对比）
    snap = save_batch_snapshot(batch, history_dir)

    print("\n---------------- 导出文件 ----------------")
    for label, p in (("批次Excel", xlsx), ("质量看板", png),
                     ("批次JSON", jpath), ("批次快照", snap)):
        print(f"{label:<10}: {p}")

    if not batch.gate_passed:
        print("\n质量门禁未通过，已阻断放行。"
              "可调整模型后重新核查，或用 --gate-profile loose/--no-gate 临时放行。",
              file=sys.stderr)
        return 3
    return 0


def _cmd_trend(args) -> int:
    history = load_project_history(
        args.history or os.path.join("output", "batch_history"), args.project)
    if not history:
        print(f"项目“{args.project}”没有历史批次记录。", file=sys.stderr)
        return 2
    print(f"项目“{args.project}”共 {len(history)} 个批次：\n")
    print(f"{'批次':<18}{'标签':<14}{'时间':<22}{'单体':>4}"
          f"{'问题':>5}{'错误':>5}{'警告':>5}  放行")
    for h in history:
        print(f"{h.get('batch_id', ''):<20}{(h.get('label') or '-'):<14}"
              f"{h.get('created_at', ''):<22}{h.get('n_files', h.get('n_units', 0)):>4}"
              f"{h.get('totals', {}).get('issues', h.get('issues', 0)):>5}"
              f"{h.get('totals', {}).get('errors', h.get('errors', 0)):>5}"
              f"{h.get('totals', {}).get('warnings', h.get('warnings', 0)):>5}  "
              f"{'✅' if h.get('gate_passed') else '⛔阻断'}")

    if len(history) >= 2:
        first, last = history[0], history[-1]
        ft, lt = first.get("totals", {}), last.get("totals", {})
        print("\n首末批次对比：")
        for key, label in (("issues", "问题总数"), ("errors", "错误"),
                           ("warnings", "警告"), ("duplicate_groups", "重复构件组"),
                           ("total_net_area", "净面积(m²)"),
                           ("rooms_open", "不闭合房间"),
                           ("opening_anomaly", "尺寸异常门窗"),
                           ("opening_unassigned", "未归属门窗")):
            o, n = ft.get(key, 0), lt.get(key, 0)
            d = n - o
            arrow = "持平" if d == 0 else (f"{'+' if d > 0 else ''}{d:g}")
            print(f"  {label:<12} {o:g} → {n:g}  （{arrow}）")

    if args.output:
        # 用历史快照的精简点复用趋势图渲染
        from types import SimpleNamespace
        fake = SimpleNamespace(
            project=args.project, trend={"history": [
                {k: h.get(k) for k in (
                    "batch_id", "label", "created_at", "gate_passed",
                    "issues", "errors", "warnings", "total_net_area")}
                for h in history]})
        p = batch_report.export_trend_chart(fake, args.output)
        print(f"\n趋势图已导出：{p}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ifc_audit",
        description="IFC 建筑模型核查工具：未闭合墙 / 重复构件 / 房间净面积 / 门窗表",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_audit = sub.add_parser("audit", help="核查 IFC 文件并导出报告")
    p_audit.add_argument("ifc", help="IFC 文件路径 (.ifc/.ifcxml/.ifczip)")
    p_audit.add_argument("-o", "--output", default="output", help="输出目录")
    p_audit.add_argument("-q", "--quiet", action="store_true", help="精简输出")
    p_audit.add_argument("--fail-on-error", action="store_true",
                         help="存在错误级问题时以退出码 1 返回（便于 CI 集成）")
    p_audit.add_argument(
        "--profile", choices=PROFILES, default="default",
        help="判定阈值预设：default=标准（默认）/ strict=严格 / loose=宽松；"
             "会被配置文件与 --set 覆盖")
    p_audit.add_argument("--config",
                         help="阈值配置 JSON 文件（可用 init-config 生成模板）")
    p_audit.add_argument(
        "--set", dest="set_threshold", action="append", default=[],
        metavar="KEY=VALUE",
                         help="单项覆盖阈值，可重复，长度 mm / 偏差 %%。"
                              "例如 --set gap_min_len_mm=50 --set area_dev_warn_pct=1")
    p_audit.set_defaults(func=_cmd_audit)

    p_gui = sub.add_parser("gui", help="启动图形界面")
    p_gui.set_defaults(func=lambda a: _launch_gui())

    # ---- 多模型批量核查 + 项目质量看板 ----
    p_batch = sub.add_parser(
        "batch",
        help="批量核查多个单体 IFC，按项目/单体/楼层汇总并生成质量看板；"
             "门禁不达标时退出码 3 阻断放行")
    p_batch.add_argument("paths", nargs="+",
                         help="IFC 文件或目录（目录取其中 .ifc/.ifcxml/.ifczip），"
                              "可混合传入多个")
    p_batch.add_argument("--project", default="未命名项目", help="项目名（看板与历史归档用）")
    p_batch.add_argument("--label", default="", help="批次标签，如 “v1提模” / “竣工审查”")
    p_batch.add_argument("-o", "--output", default=os.path.join("output", "batch"),
                         help="批次报告输出目录（默认 output/batch/）")
    p_batch.add_argument("--history", default=None,
                         help="批次历史留存目录（默认 output/batch_history/）")
    p_batch.add_argument("-q", "--quiet", action="store_true", help="精简输出")
    p_batch.add_argument("--with-3d", action="store_true",
                         help="同时导出每个单体的三维标注图（默认仅平面图，批量更快）")
    p_batch.add_argument("--no-unit-reports", action="store_true",
                         help="不导出单体 Excel/CSV/平面图，只出批次报告与看板")
    # 核查阈值（与 audit 子命令一致）
    p_batch.add_argument("--profile", choices=PROFILES, default="default",
                         help="单模型核查阈值预设：default/strict/loose")
    p_batch.add_argument("--config", help="核查阈值配置 JSON（init-config 生成）")
    p_batch.add_argument("--set", dest="set_threshold", action="append",
                         default=[], metavar="KEY=VALUE",
                         help="单项覆盖核查阈值，可重复")
    # 放行门禁
    p_batch.add_argument("--gate-profile", choices=GATE_PROFILES, default="default",
                         help="放行门禁预设：default=标准（默认）/ strict=严格 / "
                              "loose=宽松 / none=不设门禁不阻断")
    p_batch.add_argument("--gate-config", help="门禁配置 JSON（init-gate 生成）")
    p_batch.add_argument("--gate-set", action="append", default=[],
                         metavar="KEY=VALUE",
                         help="单项覆盖门禁规则，可重复，如 "
                              "--gate-set unit_max_warnings=20")
    p_batch.add_argument("--no-gate", action="store_true",
                         help="本次不启用门禁（等同 --gate-profile none，仅统计不阻断）")
    p_batch.set_defaults(func=_cmd_batch)

    # ---- 批次历史趋势 ----
    p_trend = sub.add_parser(
        "trend", help="查看项目历史批次趋势对比，可选导出趋势图 PNG")
    p_trend.add_argument("--project", required=True, help="项目名")
    p_trend.add_argument("--history", default=None,
                         help="批次历史目录（默认 output/batch_history/）")
    p_trend.add_argument("-o", "--output", default=None,
                         help="趋势图 PNG 输出路径（不给则只在控制台列出）")
    p_trend.set_defaults(func=_cmd_trend)

    p_init = sub.add_parser(
        "init-config", help="生成带说明的阈值配置文件模板（JSON）")
    p_init.add_argument("path", help="配置文件输出路径，如 thresholds.json")
    p_init.add_argument("--profile", choices=PROFILES, default="default",
                        help="模板以哪套预设值为初始值（默认 default）")
    p_init.set_defaults(func=_cmd_init_config)

    p_init_gate = sub.add_parser(
        "init-gate", help="生成带说明的项目放行门禁配置模板（JSON）")
    p_init_gate.add_argument("path", help="配置文件输出路径，如 gate.json")
    p_init_gate.add_argument("--profile", choices=GATE_PROFILES, default="default",
                             help="模板以哪套门禁预设为初始值（默认 default）")
    p_init_gate.set_defaults(func=_cmd_init_gate)

    args = parser.parse_args(argv)
    return args.func(args)


def _cmd_init_config(args) -> int:
    if os.path.exists(args.path):
        print(f"已存在同名文件，未覆盖：{args.path}", file=sys.stderr)
        return 2
    write_config_template(args.path, args.profile)
    print(f"阈值配置模板已生成：{args.path}（初始预设：{PROFILE_CN[args.profile]}）")
    print(f"修改后使用：python -m ifc_audit.cli audit model.ifc --config {args.path}")
    return 0


def _cmd_init_gate(args) -> int:
    if os.path.exists(args.path):
        print(f"已存在同名文件，未覆盖：{args.path}", file=sys.stderr)
        return 2
    write_gate_config_template(args.path, args.profile)
    print(f"门禁配置模板已生成：{args.path}（初始预设：{GATE_PROFILE_CN[args.profile]}）")
    print(f"修改后使用：python -m ifc_audit.cli batch ifc目录/ --project 项目名 "
          f"--gate-config {args.path}")
    return 0


def _launch_gui() -> int:
    try:
        from .gui import App
    except Exception as exc:
        print(f"无法启动图形界面：{exc}\n"
              "本机 Python 缺少 tkinter，请安装系统的 python3-tk，"
              "或直接使用 `python -m ifc_audit.cli audit`。", file=sys.stderr)
        return 2
    App().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
