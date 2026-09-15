"""命令行入口。

用法::

    python -m ifc_audit.cli audit model.ifc -o output/
    python -m ifc_audit.cli gui              # 图形界面
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .pipeline import audit_ifc, audit_ifc_with_config
from . import report
from .report import KIND_CN, SEV_CN
from .thresholds import (
    PROFILES, PROFILE_CN, parse_set_items, ThresholdConfigError,
    write_config_template,
)


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

    p_init = sub.add_parser(
        "init-config", help="生成带说明的阈值配置文件模板（JSON）")
    p_init.add_argument("path", help="配置文件输出路径，如 thresholds.json")
    p_init.add_argument("--profile", choices=PROFILES, default="default",
                        help="模板以哪套预设值为初始值（默认 default）")
    p_init.set_defaults(func=_cmd_init_config)

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
