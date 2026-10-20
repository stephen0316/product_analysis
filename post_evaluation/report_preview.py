"""Read-only content preview derived from the exact saved DOCX snapshot."""
from base64 import b64encode
from html import escape
from io import BytesIO
from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from docx.table import Table


def report_html(content: bytes) -> str:
    doc = Document(BytesIO(content))
    blocks = []
    for element in doc.element.body:
        if element.tag == qn("w:p"):
            paragraph = Paragraph(element, doc)
            style = paragraph.style.name
            tag = "h1" if style == "Title" else "h2" if style == "Heading 1" else "h3" if style == "Heading 2" else "h4" if style == "Heading 3" else "p"
            if paragraph.text.strip():
                blocks.append(f"<{tag}>{escape(paragraph.text).replace(chr(10), '<br>')}</{tag}>")
            for image in element.xpath(".//a:blip"):
                rid = image.get(qn("r:embed"))
                part = doc.part.related_parts.get(rid)
                if part and part.content_type in {"image/png", "image/jpeg"}:
                    data = b64encode(part.blob).decode("ascii")
                    blocks.append(f'<img alt="报告图表" src="data:{part.content_type};base64,{data}">')
        elif element.tag == qn("w:tbl"):
            table = Table(element, doc)
            rows = []
            for index, row in enumerate(table.rows):
                tag = "th" if index == 0 else "td"
                rows.append("<tr>" + "".join(f"<{tag}>{escape(cell.text)}</{tag}>" for cell in row.cells) + "</tr>")
            blocks.append('<div class="table-wrap"><table>' + "".join(rows) + "</table></div>")
    return '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'">
<style>*{box-sizing:border-box}body{margin:0;background:#eceeeb;color:#202522;font:15px/1.9 -apple-system,BlinkMacSystemFont,"Noto Sans CJK SC","Microsoft YaHei",sans-serif}article{max-width:860px;margin:22px auto;padding:48px 44px;background:white;min-height:100vh}h1{font-size:26px;line-height:1.5;margin:0 0 20px}h2{font-size:21px;margin:36px 0 14px}h3{font-size:18px;margin:25px 0 10px}h4{font-size:16px;margin:24px 0 10px}p{margin:10px 0;overflow-wrap:anywhere}img{display:block;max-width:100%;height:auto;margin:18px auto}.table-wrap{overflow:auto;margin:18px 0}table{border-collapse:collapse;font-size:12px;line-height:1.6;width:100%}td,th{border:1px solid #d9d9d9;padding:9px;text-align:center;min-width:52px}th{background:#edf0ed}td:first-child{text-align:left;min-width:90px}@media(max-width:600px){article{margin:0;padding:24px 18px}h1{font-size:22px}}</style></head><body><article>''' + "\n".join(blocks) + "</article></body></html>"
