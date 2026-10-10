from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from .store import data_root
os.environ.setdefault("MPLCONFIGDIR", str(data_root() / "matplotlib"))
(data_root() / "matplotlib").mkdir(parents=True, exist_ok=True)
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .constants import METRICS, PRODUCTS, ROLES, field_label


def make_workbook(run: dict, result: dict) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "重点产品-总分"
    ws.append([f"{run['year']}年重点产品后评估 · 阶段性结果（非正式总分）"])
    ws.append(["序号", "产品线", *[label for _, label, _ in METRICS], "总分", "排名"])
    for number, product in enumerate(PRODUCTS, 1):
        ws.append([number, product, *[result["scores"][product].get(key) for key, _, _ in METRICS], None, None])
    ws.freeze_panes = "C3"
    ws.auto_filter.ref = f"A2:N{ws.max_row}"
    ws.column_dimensions["A"].width = 8
    ws.column_dimensions["B"].width = 19
    for col in range(3, 15):
        ws.column_dimensions[get_column_letter(col)].width = 17
    _style_table(ws, 2)
    for row in ws.iter_rows(min_row=3, min_col=3, max_col=14):
        for cell in row:
            cell.alignment = Alignment(horizontal="center")
            if cell.value is not None:
                cell.number_format = "0.0"
    for col in (13, 14):
        for row in range(3, ws.max_row + 1):
            ws.cell(row, col).fill = PatternFill("solid", fgColor="F5F0E8")
    ws["B17"] = "说明：后三项评分规则待完成；总分和总排名留空，不将待完成项记为 0。"
    ws["B18"] = f"当前覆盖 {result['covered_points']}/100 分；市场份额以最新年度原值排名，其他已确认项按两级排名规则计算。"
    ws["B19"] = f"规则版本：{run.get('rule_version', 'v1-2025')}。待完成的后三项不进入正式总分。"

    detail = wb.create_sheet("计算明细")
    detail.append(["产品线", "维度", "最终名次", "最终得分", "步骤", "原值/中间值", "单位", "步骤名次", "步骤得分", "来源文件ID", "来源单元格", "公式"])
    for trace in result["traces"]:
        for step in trace["steps"]:
            source = step.get("source") or {}
            detail.append([trace["product"], next(label for key, label, _ in METRICS if key == trace["metric"]), trace["rank"], trace["score"], step["label"], step.get("value"), source.get("unit", ""), step.get("rank"), step.get("score"), source.get("file_id"), source.get("cell"), step.get("formula")])
    _style_table(detail, 1)
    detail.freeze_panes = "C2"
    detail.auto_filter.ref = detail.dimensions
    for col, width in {"A":17,"B":22,"C":12,"D":12,"E":27,"F":20,"G":12,"H":12,"I":12,"J":35,"K":22,"L":60}.items():
        detail.column_dimensions[col].width = width

    issues = wb.create_sheet("校验与待确认")
    issues.append(["级别", "类型", "产品线", "字段", "说明"])
    for issue in result["issues"]:
        issues.append([issue.get("level"), issue.get("code"), issue.get("product"), issue.get("field"), issue.get("message")])
    for key, label, group in METRICS:
        if group is None:
            issues.append(["pending", "rule_pending", None, key, f"{label}评分规则待确认"])
    _style_table(issues, 1)
    for col, width in {"A":13,"B":20,"C":18,"D":25,"E":90}.items():
        issues.column_dimensions[col].width = width

    sources = wb.create_sheet("数据来源")
    sources.append(["文件ID", "文件名", "数据类别", "SHA256", "映射工作表", "表头行", "产品列", "字段映射", "上传时间"])
    for file in run["files"]:
        mapping = file["mapping"]
        sources.append([file["id"], file["name"], file["role"], file["sha256"], mapping.get("sheet"), mapping.get("header_row"), mapping.get("product_col"), str(mapping.get("fields")), file["uploaded_at"]])
    _style_table(sources, 1)
    for col, width in {"A":35,"B":65,"C":16,"D":65,"E":25,"F":12,"G":12,"H":110,"I":30}.items():
        sources.column_dimensions[col].width = width
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def _style_table(ws, header_row: int) -> None:
    for cell in ws[header_row]:
        cell.fill = PatternFill("solid", fgColor="203447")
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.row_dimensions[header_row].height = 30


def make_report(run: dict, result: dict) -> bytes:
    doc = Document()
    sec = doc.sections[0]
    sec.top_margin = Inches(.78)
    sec.bottom_margin = Inches(.7)
    sec.left_margin = Inches(.7)
    sec.right_margin = Inches(.7)
    styles = doc.styles
    for name in ("Normal", "Title", "Heading 1", "Heading 2"):
        style = styles[name]
        style.font.name = "Arial Unicode MS"
        style.font.color.rgb = RGBColor(0, 0, 0)
        style._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "Arial Unicode MS")
    styles["Normal"].font.size = Pt(10.5)
    styles["Title"].font.size = Pt(20)
    styles["Title"].font.bold = True
    styles["Heading 1"].font.size = Pt(14)
    styles["Heading 2"].font.size = Pt(11)
    title_border = styles["Title"]._element.get_or_add_pPr().find(qn("w:pBdr"))
    if title_border is not None:
        styles["Title"]._element.pPr.remove(title_border)
    doc.add_paragraph(f"{run['year']}年重点产品后评估报告", style="Title")
    doc.add_paragraph("阶段性测算稿 · 待负责人审阅")
    doc.add_paragraph(f"评估范围：13 条重点产品线。规则版本 {run.get('rule_version', 'v1-2025')}。已确认并计算 7 个维度，覆盖 {result['covered_points']}/100 分。自研占比／ROI、能力评测、品质服务规则待完成，本稿不生成正式总分和总排名。")
    doc.add_heading("一 上年度评估改善情况", 1)
    doc.add_paragraph("本批次尚未录入上年度评估排名、改善措施及完成情况。该部分留待负责人补充依据并审阅，不根据本年度分数推断改善效果。")
    doc.add_heading("二 本年度产品后评估结果", 1)
    doc.add_heading("评估口径与数据来源", 2)
    doc.add_paragraph("市场份额：取最新年度原值排名。经济效益：当年值与相对增幅分别排名赋分，取平均后再次排名赋分。产品三效：劳产、元效、人效分别排名赋分，取平均后再次排名赋分。均采用并列跳位排名；第一名 10 分，每降一名减 0.5 分。")
    doc.add_paragraph("零基数增幅无法计算；负基数仍按既定公式计算，并保留复核提示。待完成项不计零分。")
    for file in run["files"]:
        doc.add_paragraph(f"• {file['name']}｜{ROLES.get(file['role'], file['role'])}｜{file['sha256'][:12]}…")
    doc.add_page_break()
    doc.add_heading("重点产品评分结果", 2)
    table = doc.add_table(rows=1, cols=8)
    table.style = "Light Shading Accent 1"
    table.autofit = False
    widths = [Inches(1.15)] + [Inches(.84)] * 7
    for row in table.rows:
        for cell, width in zip(row.cells, widths):
            cell.width = width
    headers = ["产品线"] + [label for _, label, group in METRICS if group is not None]
    for cell, label in zip(table.rows[0].cells, headers):
        cell.text = label
    for product in PRODUCTS:
        row = table.add_row().cells
        for cell, width in zip(row, widths):
            cell.width = width
        row[0].text = product
        for index, (key, _, group) in enumerate((item for item in METRICS if item[2] is not None), 1):
            score = result["scores"][product].get(key)
            row[index].text = f"{score:.1f}" if score is not None else "—"
    for row in table.rows:
        for index, cell in enumerate(row.cells):
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            for paragraph in cell.paragraphs:
                paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT if index == 0 else WD_ALIGN_PARAGRAPH.CENTER
                for text_run in paragraph.runs:
                    text_run.font.name = "Arial Unicode MS"
                    text_run.font.size = Pt(8)
                    text_run._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "Arial Unicode MS")
    doc.add_paragraph("注：上表为已确认维度的分项得分，不代表正式总分。完整计算过程与来源单元格见配套评分总表。")
    available = [(key, label) for key, label, group in METRICS if group is not None and result["metrics"][[m[0] for m in METRICS].index(key)]["status"] == "已计算"]
    if available:
        fig, ax = plt.subplots(figsize=(7.1, 2.6))
        labels = [f"{i+1}" for i in range(len(available))]
        averages = [sum(result["scores"][p][key] for p in PRODUCTS) / len(PRODUCTS) for key, _ in available]
        bars = ax.bar(labels, averages, color="#6d8b75", width=.55)
        ax.bar_label(bars, fmt="%.1f", padding=3, fontsize=8)
        ax.set_ylim(0, 10.6)
        font_file = Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf")
        if font_file.exists():
            chinese_font = font_manager.FontProperties(fname=str(font_file))
            ax.set_ylabel("平均得分", fontsize=9, fontproperties=chinese_font)
            ax.set_title("已计算维度平均得分", loc="left", fontsize=10, fontproperties=chinese_font)
        else:
            ax.set_ylabel("Average score", fontsize=9)
            ax.set_title("Average by calculated dimension", loc="left", fontsize=10)
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", color="#e4e9e8", linewidth=.5)
        ax.set_axisbelow(True)
        fig.tight_layout()
        chart = BytesIO()
        fig.savefig(chart, format="png", dpi=180)
        plt.close(fig)
        chart.seek(0)
        doc.add_picture(chart, width=Inches(6.7))
        doc.add_paragraph("图注：" + "；".join(f"{i+1}={label}" for i, (_, label) in enumerate(available)) + "。仅作分项展示，不形成总分。")
    doc.add_heading("数据校验与待确认事项", 2)
    errors = [issue for issue in result["issues"] if issue["level"] == "error"]
    warnings = [issue for issue in result["issues"] if issue["level"] == "warning"]
    doc.add_paragraph(f"数据错误 {len(errors)} 项，复核提示 {len(warnings)} 项。")
    for issue in errors[:10]:
        doc.add_paragraph(f"• {issue['message']}")
    if len(errors) > 10:
        doc.add_paragraph("其余错误见评分总表的“校验与待确认”工作表。")
    warning_groups: dict[str, list[str]] = {}
    for issue in warnings:
        warning_groups.setdefault(issue.get("field", "其他"), []).append(issue.get("product", ""))
    for field, products in warning_groups.items():
        doc.add_paragraph(f"• {field_label(field)}负基数：{'、'.join(products)}。仍按既定相对增幅公式计算，经营含义待复核。")
    doc.add_paragraph("待确认规则：自研占比／ROI、能力评测、品质服务。缺少上年排名、产品矩阵与人员调整依据，本稿不自动编造对应结论。")
    doc.add_heading("三 下一步工作计划", 1)
    notes = run.get("notes") or {}
    for heading, key in (("经营原因分析", "analysis"), ("产品处置建议", "recommendations"), ("负责人审阅意见", "review")):
        doc.add_heading(heading, 2)
        doc.add_paragraph(notes.get(key) or "待负责人填写并确认。")
    doc.add_heading("附录 计算明细与来源", 1)
    doc.add_paragraph("每条产品线的原值、增幅、两级名次、得分和来源单元格见配套评分总表的“计算明细”及“数据来源”工作表。")
    footer = sec.footer.paragraphs[0]
    footer.text = "产品后评估 · 阶段性测算稿 · 人工结论须审阅"
    out = BytesIO()
    doc.save(out)
    return out.getvalue()
