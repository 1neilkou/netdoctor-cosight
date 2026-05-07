# File Reading Skill

当 step 需要读取本地附件文件（docx/pdf/xlsx等）时使用。

## 执行顺序
1. 首先检查 COSIGHT_ATTACHMENT_PATH 环境变量或 prompt 里的文件路径
2. 优先用 extract_document_content 工具读取
3. 如果失败，用 execute_code 直接读取：
   - docx: python-docx 库
   - pdf: 用 execute_code 调用 pdfplumber 或 PyPDF2
   - xlsx: openpyxl 或 pandas
4. 如果工具调用全部失败，在 step_notes 里明确说明无法读取，不要猜测文件内容

## 示例代码（docx）
```python
from docx import Document
doc = Document('{file_path}')
for para in doc.paragraphs:
    print(para.text)
```

