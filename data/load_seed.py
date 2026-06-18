'''seed 데이터 -> PostgreSQL'''

import psycopg2
import uuid
import pathlib
from psycopg2.extras import execute_values
from dotenv import load_dotenv
import json
import os

load_dotenv()

SEED_FOLDERS = ['competitors', 'pricing', 'reviews']
BASE_DIR = pathlib.Path(__file__).parent

FOLDER_SOURCE_MAP = {
    'competitors': 'seed_competitor',
    'pricing':     'seed_pricing',
    'reviews':     'seed_review',
}

FOLDER_KIND_MAP = {
    'competitors': 'competitor_analysis',
    'pricing':     'pricing_data',
    'reviews':     'review_data',
}


def connect_db():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST'),
        port=os.getenv('POSTGRES_PORT'),
        dbname=os.getenv('POSTGRES_DB'),
        user=os.getenv('POSTGRES_USER'),
        password=os.getenv('POSTGRES_PASSWORD')
    )


# ─────────────────────────────────────────
# Step 1. 로컬 파일 로드
# ─────────────────────────────────────────

def _build_clean_text(row: dict) -> str:
    """clean_text에 meta 구조화 정보를 append해 데이터 손실 방지."""
    base_text = row.get('clean_text', '')
    meta = row.get('meta')
    if not meta:
        return base_text

    # meta를 사람이 읽기 좋은 텍스트로 변환해 clean_text 뒤에 붙임
    meta_lines = [f'{k}: {v}' for k, v in meta.items() if v is not None]
    if not meta_lines:
        return base_text

    return base_text + '\n\n---\n' + '\n'.join(meta_lines)


def load_seed_from_local():
    all_rows = []
    validation_errors = []

    for folder in SEED_FOLDERS:
        folder_path = BASE_DIR / folder
        files = sorted(folder_path.glob('*.json'))

        for f in files:
            with open(f, encoding='utf-8') as fp:
                data = json.load(fp)
            row = data[0] if isinstance(data, list) else data

            # 검증 1: source_type 키 존재 및 폴더-source_type 일치
            source_type = row.get('source_type')
            expected_source = FOLDER_SOURCE_MAP[folder]
            if not source_type:
                validation_errors.append(f'{folder}/{f.stem}.json: source_type 없음')
                continue
            if source_type != expected_source:
                validation_errors.append(
                    f'{folder}/{f.stem}.json: source_type="{source_type}" (기대값: "{expected_source}")'
                )
                continue

            # 검증 2: title 유효성
            if not row.get('title', '').strip():
                validation_errors.append(f'{folder}/{f.stem}.json: title 없음 또는 빈 값')
                continue

            # document_kind 주입 (폴더 기반)
            row['_document_kind'] = FOLDER_KIND_MAP[folder]
            # meta.source → canonical_url 매핑
            row['_canonical_url'] = (row.get('meta') or {}).get('source')
            # meta 구조화 정보를 clean_text에 병합 (스키마 변경 없이 데이터 보존)
            row['_clean_text'] = _build_clean_text(row)
            # 중복 체크용 키: DB 쿼리가 lower(title)을 사용하므로 일치시킴
            row['_dedup_key'] = row.get('title', '').strip().lower()
            all_rows.append(row)

        print(f'✅ {folder}: {len(files)}건 로드')

    if validation_errors:
        print(f'\n⚠️  사전 검증 실패 {len(validation_errors)}건:')
        for err in validation_errors:
            print(f'  - {err}')
        print()

    print(f'✅ 총 {len(all_rows)}건 로드 완료 (검증 실패 {len(validation_errors)}건 제외)')
    return all_rows, len(validation_errors)


# ─────────────────────────────────────────
# Step 2. RDS 저장 (배치 처리)
# ─────────────────────────────────────────

def save_seed_to_db(rows, batch_size=50):
    conn = connect_db()
    cursor = conn.cursor()

    success = 0
    skip = 0
    fail = 0

    for batch_idx in range(0, len(rows), batch_size):
        batch = rows[batch_idx:batch_idx + batch_size]

        try:
            # 중복 체크: (source_type, lower(title)) 조합 — _dedup_key와 동일한 기준
            pairs = [(row.get('source_type'), row['_dedup_key']) for row in batch]
            ph = ','.join(['(%s,%s)'] * len(pairs))
            cursor.execute(f"""
                SELECT source_type, lower(title) FROM documents
                WHERE (source_type, lower(title)) IN ({ph})
            """, [val for pair in pairs for val in pair])
            existing = {(r[0], r[1]) for r in cursor.fetchall()}

            new_rows = []
            for row in batch:
                key = (row.get('source_type'), row['_dedup_key'])
                if key in existing:
                    skip += 1
                    continue

                meta = row.get('meta')
                new_rows.append((
                    str(uuid.uuid4()),                    # document_id
                    row.get('source_type'),               # source_type
                    row.get('ext_id'),                    # ext_id
                    row.get('title', '(제목 없음)'),       # title
                    row.get('_canonical_url'),             # canonical_url ← meta.source
                    row['_clean_text'],                    # clean_text ← 원본 + meta 텍스트
                    json.dumps(meta, ensure_ascii=False) if meta else None,  # meta (jsonb)
                    0.6,                                   # reliability_score
                    0.7,                                   # freshness_score
                    False                                  # is_user_provided
                ))

            if new_rows:
                execute_values(cursor, """
                    INSERT INTO documents (
                        document_id, source_type, ext_id,
                        title, canonical_url, clean_text, meta,
                        reliability_score, freshness_score,
                        is_user_provided
                    )
                    VALUES %s
                """, new_rows)

            conn.commit()
            success += len(new_rows)
            batch_skip = len(batch) - len(new_rows)
            print(f'✅ 배치 {batch_idx // batch_size + 1} 완료: 신규 {len(new_rows)}건 / 스킵 {batch_skip}건')

        except Exception as e:
            print(f'❌ 배치 저장 실패 (시작: {batch_idx}): {e}')
            conn.rollback()
            fail += len(batch)

    cursor.close()
    conn.close()
    print(f'\n✅ 저장 완료: {success}건 성공 / {skip}건 스킵(중복) / {fail}건 실패')


# ─────────────────────────────────────────
# Step 3. 결과 확인
# ─────────────────────────────────────────

def verify(excluded=0):
    """excluded: 사전 검증에서 제외된 파일 수 (verify의 expected에서 차감)"""
    conn = connect_db()
    cursor = conn.cursor()

    print('\n=== 시드 코퍼스 적재 결과 ===')
    for source_type in ['seed_competitor', 'seed_pricing', 'seed_review']:
        cursor.execute(
            'SELECT COUNT(*) FROM documents WHERE source_type = %s',
            (source_type,)
        )
        count = cursor.fetchone()[0]
        print(f'{source_type:<22}: {count}건')

    cursor.execute("SELECT COUNT(*) FROM documents WHERE source_type LIKE 'seed%'")
    seed_total = cursor.fetchone()[0]
    print(f'{"시드 합계":<22}: {seed_total}건')

    cursor.execute('SELECT COUNT(*) FROM documents')
    all_total = cursor.fetchone()[0]
    print(f'{"documents 전체":<22}: {all_total}건')
    print('==============================')

    total_files = sum(
        len(list((BASE_DIR / folder).glob('*.json')))
        for folder in SEED_FOLDERS
    )
    expected = total_files - excluded  # 검증 실패 제외분 반영

    if seed_total >= expected:
        print(f'✅ 시드 코퍼스 적재 완료! ({seed_total}/{expected}건)')
    else:
        print(f'⚠️  {expected - seed_total}건 미적재 ({seed_total}/{expected}건)')
    if excluded:
        print(f'ℹ️  사전 검증 실패로 제외된 파일: {excluded}건 (전체 {total_files}건 중)')

    cursor.close()
    conn.close()


# ─────────────────────────────────────────
# 실행
# ─────────────────────────────────────────

if __name__ == '__main__':
    print('=== Step 1. 로컬 파일 로드 ===')
    rows, excluded = load_seed_from_local()

    print('\n=== Step 2. RDS 저장 ===')
    save_seed_to_db(rows)

    print('\n=== Step 3. 결과 확인 ===')
    verify(excluded=excluded)
