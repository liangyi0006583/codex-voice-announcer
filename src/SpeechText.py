"""Readable speech text and bounded, sentence-aware neural generation batches."""
import re
import unicodedata


def readable_text(value):
    text = str(value or '')
    # Technical strings remain visible in history; these replacements are speech-only.
    text = re.sub(r'(?i)(?:sha[- ]?256|哈希|清单摘要)\s*[:：]?\s*[a-f0-9]{32,}',
                  '校验值请查看任务', text)
    text = re.sub(r'(?<![\w])[a-fA-F0-9]{32,}(?![\w])', '校验值请查看任务', text)
    text = re.sub(r'(?<![\w])[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}(?![\w])',
                  '编号请查看任务', text)
    text = re.sub(r'(?:~?/|[A-Za-z]:\\)[A-Za-z0-9_.\-/\\]+\.(?:py|json|md|yaml|yml|csv|xlsx|sqlite)\b',
                  '文件路径请查看任务', text)
    text = re.sub(r'\b[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+\.(?:py|json|md|yaml|yml|csv|xlsx|sqlite)\b',
                  '文件路径请查看任务', text)
    replacements = {
        'PENDING_DIRECT_HUMAN_APPROVAL': '等待人工批准', 'PENDINGDIRECTHUMANAPPROVAL': '等待人工批准',
        'SP-API': '销售伙伴接口', 'API': '接口', 'Listing': '商品页面',
        'SKU': '商品编号', 'ASIN': '商品识别码', 'OAuth': '授权', 'HTTP': '网络请求',
        'schema': '数据规范', 'manifest': '清单', 'Preview': '预览', 'AES': '加密',
        'UNKNOWN': '未知', 'blocked': '受阻', 'HOLD': '暂缓', 'PASS': '通过',
        'FAIL': '失败', 'GET': '查询', 'POST': '提交', 'PR': '合并请求',
        'commit': '代码提交', 'push': '上传代码', 'snapshot': '快照', 'Goal': '目标',
    }
    for token, phrase in replacements.items():
        text = re.sub(r'(?<![A-Za-z])' + re.escape(token) + r'(?![A-Za-z])', phrase, text, flags=re.I)
    text = re.sub(r'(?<![A-Za-z0-9])[A-Za-z0-9][A-Za-z0-9_-]{22,}(?![A-Za-z0-9])',
                  '长编号请查看任务', text)
    text = re.sub(r'\b([vV])(\d+)\b', r'第\2版', text)
    text = re.sub(r'(\d+)\s*/\s*(\d+)', r'\1 项，共 \2 项', text)
    # A sequence of identical replacement notes should be spoken once.
    text = re.sub(r'((?:文件路径|校验值|长编号|编号)请查看任务)(?:[、，,；;\s]+\1)+', r'\1', text)
    return re.sub(r'[ \t]+', ' ', text).strip()


def speech_batches(text, maximum=72):
    text = readable_text(text)
    sentences = [s.strip() for s in re.findall(r'[^。！？!?\n]+[。！？!?]?', text) if s.strip()]
    pieces = []
    for sentence in sentences:
        while len(sentence) > maximum:
            cuts = [sentence.rfind(p, maximum//2, maximum) for p in '，；：,;: ']
            cut = max(cuts)
            # Prefer punctuation but bound pathological unpunctuated paragraphs too.
            cut = cut + 1 if cut >= 0 else maximum
            pieces.append(sentence[:cut].strip()); sentence = sentence[cut:].strip()
        if sentence: pieces.append(sentence)
    batches = []
    for piece in pieces:
        if batches and len(batches[-1]) + len(piece) <= maximum:
            batches[-1] += piece
        else:
            batches.append(piece)
    return batches


def normalize_content(text):
    return ''.join(c.lower() for c in unicodedata.normalize('NFKC', text) if c.isalnum())
