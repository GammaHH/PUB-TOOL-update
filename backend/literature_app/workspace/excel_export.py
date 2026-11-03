"""Application XLSX export using the project's existing openpyxl dependency."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

from .literature import LiteratureStore


HEADERS = [
    '順序 / Order', 'PMID', '標題 / Title', '摘要 / Abstract', '摘要狀態 / Abstract status',
    '作者 / Authors', '期刊 / Journal', '年份 / Year', 'DOI', 'PMCID', 'PubMed URL',
    'MeSH terms', 'Keywords', 'Publication types', '篩選 / Screening', '備註 / Notes',
    '排除理由 / Exclusion reason', '抓取時間 UTC / Fetched', '抓取錯誤 / Fetch error',
    '搜尋紀錄 / Run ID',
]


def export_xlsx(store, project_id: int, run_id: int, target: Path, scope: str = 'ALL'):
    if scope not in {'ALL', 'KEEP', 'MAYBE', 'EXCLUDE', 'UNREVIEWED'}:
        raise ValueError('不支援的匯出範圍')
    target = Path(target)
    if target.suffix.lower() != '.xlsx':
        raise ValueError('請選擇 .xlsx 檔案')
    if target.exists():
        raise ValueError('檔案已存在，請另選新檔名；不覆寫既有匯出')
    with store._connect() as db:
        db.execute('BEGIN')
        run = LiteratureStore(store).require_run(db, project_id, run_id)
        details = dict(db.execute('SELECT * FROM pubmed_run_details WHERE run_id=?', (run_id,)).fetchone())
        project = db.execute('SELECT name FROM projects WHERE id=?', (project_id,)).fetchone()['name']
        articles = [dict(r) for r in db.execute('''SELECT m.pmid AS member_pmid,m.ordinal,a.*,
            COALESCE(s.status,'UNREVIEWED') AS screening_status,COALESCE(s.notes,'') AS screening_notes,
            COALESCE(s.exclusion_reason,'') AS screening_reason,f.error AS fetch_error
            FROM search_run_pmids m LEFT JOIN articles a ON a.pmid=m.pmid
            LEFT JOIN screenings s ON s.article_id=a.id AND s.project_id=?
            LEFT JOIN pubmed_fetch_failures f ON f.run_id=m.search_run_id AND f.pmid=m.pmid
            WHERE m.search_run_id=? ORDER BY m.ordinal''', (project_id, run_id))]
    chosen = [a for a in articles if scope == 'ALL' or a['screening_status'] == scope]
    wb = Workbook()
    ws = wb.active
    ws.title = 'Articles'
    evidence = wb.create_sheet('Search')
    long_rows = []

    def put(sheet, row, column, value, record, field):
        cell = sheet.cell(row, column)
        if isinstance(value, str):
            if ILLEGAL_CHARACTERS_RE.search(value):
                raise ValueError(f'{record} / {field} 含 Excel 不支援的控制字元；未靜默刪除')
            if len(value) > 32767:
                parts = [value[i:i + 32767] for i in range(0, len(value), 32767)]
                long_rows.extend((record, field, i + 1, len(parts), part) for i, part in enumerate(parts))
                value = parts[0]
            cell.value = value
            # Titles, notes and query strings must remain literal, even if '=...'.
            cell.data_type = 's'
        else:
            cell.value = value
        cell.alignment = Alignment(vertical='top', wrap_text=True)

    for col, title in enumerate(HEADERS, 1):
        put(ws, 1, col, title, 'Headers', title)
    for index, a in enumerate(chosen, 2):
        pmid = a['member_pmid']
        status = ('FETCH_FAILED' if a['fetch_error'] else 'NOT_FETCHED') if a['id'] is None else ('AVAILABLE' if a['abstract'] else 'NO_ABSTRACT')
        def unpack(key):
            return '; '.join(json.loads(a[key])) if a[key] else None
        values = [a['ordinal'] + 1, pmid, a['title'], a['abstract'], status, unpack('authors_json'),
                  a['journal'], a['publication_year'], a['doi'], a['pmcid'], f'https://pubmed.ncbi.nlm.nih.gov/{pmid}/',
                  unpack('mesh_terms_json'), unpack('keywords_json'), unpack('publication_types_json'),
                  a['screening_status'], a['screening_notes'], a['screening_reason'], a['fetched_at'], a['fetch_error'], run_id]
        for col, (field, value) in enumerate(zip(HEADERS, values), 1):
            put(ws, index, col, value, pmid, field)
        ws.cell(index, 11).hyperlink = values[10]
        ws.cell(index, 11).style = 'Hyperlink'
        ws.row_dimensions[index].height = 110
    exported_at = datetime.now(timezone.utc).isoformat()
    metadata = [
        ('Project', project), ('Project ID', project_id), ('Run ID', run_id), ('Original query', run['query']),
        ('Query translation', details['query_translation']), ('Query hash SHA256', run['query_hash']),
        ('Searched UTC', run['searched_at']), ('NCBI total count', run['result_count']),
        ('Collected PMID count', details['collected_count']), ('Requested limit', details['requested_limit']),
        ('Complete PMID collection', details['collected_count'] == run['result_count']),
        ('Export scope', scope), ('Exported article rows', len(chosen)), ('Exported UTC', exported_at),
        ('NCBI warnings', details['warnings_json']),
        ('Abstract source', 'PubMed primary AbstractText; not full text. Empty abstracts are retained with an explicit status.'),
        ('Long text policy', 'Excel limit: 32767 characters per cell. Longer fields show part 1 here; LongText contains ALL parts. Join Text in Part order without adding separators.'),
        ('Screening source', 'Saved project-level decisions only. Unsaved notes and UI filters are not automatically included.'),
    ]
    for row, (key, value) in enumerate([('Field', 'Value'), *metadata], 1):
        put(evidence, row, 1, key, 'Search', key)
        put(evidence, row, 2, value, 'Search', key)
    if long_rows:
        long_sheet = wb.create_sheet('LongText')
        long_sheet.append(['PMID / Record', 'Field', 'Part', 'Total parts', 'Text'])
        for row in long_rows:
            long_sheet.append(row)
            for cell in long_sheet[long_sheet.max_row]:
                if isinstance(cell.value, str):
                    cell.data_type = 's'
                cell.alignment = Alignment(vertical='top', wrap_text=True)
        long_sheet.column_dimensions['B'].width = 32
        long_sheet.column_dimensions['E'].width = 100
    for sheet in wb:
        sheet.freeze_panes = 'A2'
        sheet.auto_filter.ref = sheet.dimensions
        sheet.row_dimensions[1].height = 30
        for cell in sheet[1]:
            cell.fill = PatternFill('solid', fgColor='243D58')
            cell.font = Font(color='FFFFFF', bold=True)
            cell.alignment = Alignment(wrap_text=True, vertical='center')
        for col in range(1, sheet.max_column + 1):
            key = get_column_letter(col)
            sheet.column_dimensions[key].width = 22
    ws.column_dimensions['C'].width = 60
    ws.column_dimensions['D'].width = 100
    ws.column_dimensions['F'].width = 40
    ws.column_dimensions['P'].width = 45
    evidence.column_dimensions['A'].width = 32
    evidence.column_dimensions['B'].width = 115
    if long_rows:
        wb['LongText'].column_dimensions['B'].width = 32
        wb['LongText'].column_dimensions['E'].width = 100
    # Exclusive creation prevents clobbering an existing file even after dialog.
    with target.open('xb') as stream:
        wb.save(stream)
    check = load_workbook(target, read_only=True, data_only=False)
    try:
        if check['Articles'].max_row != len(chosen) + 1 or check['Articles']['B1'].value != 'PMID':
            raise ValueError('匯出後結構驗證失敗；請保留檔案與記錄')
    finally:
        check.close()
    with store._connect() as db:
        db.execute('INSERT INTO export_runs(project_id,format,path,exported_at,row_count) VALUES(?,?,?,?,?)',
                   (project_id, 'xlsx', str(target), exported_at, len(chosen)))
    return {'path': str(target), 'rows': len(chosen), 'long_text_parts': len(long_rows), 'cancelled': False}
