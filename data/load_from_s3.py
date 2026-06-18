'''s3 -> PostgreSQL'''

# 모듈 가져오기
import psycopg2
import boto3
import json
import uuid
import re
from psycopg2.extras import execute_values
from dotenv import load_dotenv
import os

# 환경변수 로드
load_dotenv()

# DB 연결 함수
def connect_db():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST'),
        port=os.getenv('POSTGRES_PORT'),
        dbname=os.getenv('POSTGRES_DB'),
        user=os.getenv('POSTGRES_USER'),
        password=os.getenv('POSTGRES_PASSWORD'),
        keepalives=1,
        keepalives_idle=60,
        keepalives_interval=10,
        keepalives_count=5
    )

# s3 클라이언트 함수
def connect_s3():
    return boto3.client('s3',
        region_name=os.getenv('AWS_REGION')
    )

# 청구항 파싱 함수
def parse_limitations(claim_text):
    if not claim_text:
        return []
    text = re.sub(r'^\d+\.\s*', '', claim_text).strip() # 청구항 번호 및 공백 제거
    parts = text.split(';') # ;으로 나누기
    limitations = []
    for part in parts:
        part = re.sub(r'\s+', ' ', part).strip() # 줄바꿈, 탭, 연속 공백 -> 공백 1칸으로 통일
        if len(part) >= 10:
            limitations.append(part)
    return limitations

# 독립항, 종속항 계급 구조 파악 함수
def parse_claims(claim_text):
    if not claim_text:
        return []
    claim_text = re.sub(
        r'^.*?(?=\b1\s*\.)', '', claim_text, flags=re.DOTALL
    ).strip() # 청구항 번호 나올 때까지 앞에 서두 날리기, 앞뒤 공백 제거
    parts = re.split(r'\b(\d+)\s*\.\s*', claim_text) # 청구항 번호별로 쪼개기
    claims = []
    i = 1
    while i < len(parts) - 1:
        try:
            claim_no = int(parts[i])
            claim_body = parts[i + 1].strip() if i + 1 < len(parts) else ''
            if not claim_body:
                i += 2
                continue
            full_claim_text = f"{claim_no}. {claim_body}"
            parent_match = re.search(
                r'\b(?:of|in|to|according\s+to|as\s+(?:in|recited\s+in))\s+claims?\s+(\d+)',
                claim_body, re.IGNORECASE
            ) # 종속항 여부 확인
            parent_claim_no = int(parent_match.group(1)) if parent_match else None
            is_independent = parent_claim_no is None
            claims.append({
                'claim_no':        claim_no,
                'claim_text':      full_claim_text,
                'is_independent':  is_independent,
                'parent_claim_no': parent_claim_no
            })
        except (ValueError, IndexError):
            pass
        i += 2
    return claims

# s3 파일 불러오는 함수
def load_from_s3(filename):
    s3 = connect_s3()
    response = s3.get_object(
        Bucket=os.getenv('S3_BUCKET_NAME'),
        Key=filename
    )
    rows = json.loads(response['Body'].read().decode('utf-8'))
    print(f"✅ S3에서 {len(rows)}건 로드")
    return rows

# DB 저장 함수
def save_to_db(rows):
    conn = connect_db()
    cursor = conn.cursor()

    success = 0
    fail = 0
    batch_size = 100

    for batch_idx in range(0, len(rows), batch_size):
        batch = rows[batch_idx:batch_idx + batch_size]

        try:
            # ── Step 1: 기존 documents 일괄 조회 (중복 체크) ──
            pub_nums = [row['publication_number'] for row in batch]
            placeholders = ','.join(['%s'] * len(pub_nums))
            cursor.execute(f"""
                SELECT ext_id, document_id FROM documents
                WHERE ext_id IN ({placeholders})
            """, pub_nums)
            existing_docs = {row[0]: row[1] for row in cursor.fetchall()} # row[0] -> ext_id, row[1] -> document_id

            # ── Step 2: 새 documents만 배치 INSERT ──
            doc_data = []
            doc_map = {}

            for row in batch:
                pub_num = row['publication_number']

                # 이미 있으면 스킵
                if pub_num in existing_docs:
                    doc_map[pub_num] = existing_docs[pub_num]
                    continue

                document_id = str(uuid.uuid4())

                try:
                    year = int(str(row['filing_date'])[:4])
                    freshness = 0.8 if year >= 2022 else 0.5
                except:
                    freshness = 0.5

                meta = json.dumps({
                    'assignee':    row.get('assignee') or '(출원인 미상)',
                    'filing_date': str(row['filing_date']),
                    'grant_date':  str(row.get('grant_date')),
                    'cpc_code':    row.get('cpc_code')
                }, ensure_ascii=False)

                doc_data.append((
                    document_id, 'patent', pub_num,
                    row.get('title') or '(제목 없음)',
                    row.get('abstract') or '(초록 없음)',
                    meta,
                    0.9, freshness, False
                ))
                doc_map[pub_num] = document_id

            if doc_data:
                execute_values(cursor, """
                    INSERT INTO documents (
                        document_id, source_type, ext_id,
                        title, clean_text, meta,
                        reliability_score, freshness_score,
                        is_user_provided
                    )
                    VALUES %s
                    ON CONFLICT (ext_id) DO NOTHING
                """, doc_data)

            # ── Step 3: 기존 patent_claims 일괄 조회 ──
            doc_ids_all = list(doc_map.values())
            if doc_ids_all:
                placeholders = ','.join(['%s'] * len(doc_ids_all))
                cursor.execute(f"""
                    SELECT document_id, claim_no, claim_id FROM patent_claims
                    WHERE document_id IN ({placeholders})
                """, doc_ids_all)
                existing_claims = {(row[0], row[1]): row[2] for row in cursor.fetchall()}
            else:
                existing_claims = {}

            # ── Step 4: 새 patent_claims만 배치 INSERT ──
            claims_data = []
            claim_map = {}

            for row in batch:
                pub_num = row['publication_number']
                document_id = doc_map.get(pub_num)
                if not document_id:
                    continue

                claim_text = row.get('claim_text') or ''
                parsed_claims = parse_claims(claim_text)

                if not parsed_claims:
                    print(f"⚠️  {pub_num}: 청구항 파싱 실패")

                for c in parsed_claims:
                    key = (document_id, c['claim_no'])

                    # 이미 있으면 스킵
                    if key in existing_claims:
                        claim_map[key] = existing_claims[key]
                        continue

                    claim_id = str(uuid.uuid4())
                    claims_data.append((
                        claim_id, document_id,
                        c['claim_no'], c['claim_text'],
                        c['is_independent'], c['parent_claim_no']
                    ))
                    claim_map[key] = claim_id

            if claims_data:
                execute_values(cursor, """
                    INSERT INTO patent_claims (
                        claim_id, document_id,
                        claim_no, claim_text,
                        is_independent, parent_claim_no
                    )
                    VALUES %s
                    ON CONFLICT (document_id, claim_no) DO NOTHING
                """, claims_data)

            # ON CONFLICT DO NOTHING으로 스킵된 claim은 우리가 생성한 claim_id가
            # DB에 없으므로, INSERT 후 DB에서 실제 claim_id를 다시 조회하여 갱신
            if doc_ids_all:
                placeholders = ','.join(['%s'] * len(doc_ids_all))
                cursor.execute(f"""
                    SELECT document_id, claim_no, claim_id FROM patent_claims
                    WHERE document_id IN ({placeholders})
                """, doc_ids_all)
                claim_map = {(row[0], row[1]): row[2] for row in cursor.fetchall()}

            # ── Step 5: 기존 claim_limitations 일괄 조회 ──
            claim_ids_all = list(claim_map.values())
            if claim_ids_all:
                placeholders = ','.join(['%s'] * len(claim_ids_all))
                cursor.execute(f"""
                    SELECT claim_id, limitation_order FROM claim_limitations
                    WHERE claim_id IN ({placeholders})
                """, claim_ids_all)
                existing_limitations = {(row[0], row[1]) for row in cursor.fetchall()}
            else:
                existing_limitations = set()

            # ── Step 6: 새 claim_limitations만 배치 INSERT ──
            limitations_data = []

            for row in batch:
                pub_num = row['publication_number']
                document_id = doc_map.get(pub_num)
                if not document_id:
                    continue

                claim_text = row.get('claim_text') or ''
                parsed_claims = parse_claims(claim_text)

                for c in parsed_claims:
                    key = (document_id, c['claim_no'])
                    claim_id = claim_map.get(key)
                    if not claim_id:
                        continue

                    limitations = parse_limitations(c['claim_text'])
                    for order, limitation_text in enumerate(limitations, 1):
                        # 이미 있으면 스킵
                        if (claim_id, order) in existing_limitations:
                            continue

                        limitation_id = str(uuid.uuid4())
                        limitations_data.append((
                            limitation_id, claim_id,
                            order, limitation_text
                        ))

            if limitations_data:
                execute_values(cursor, """
                    INSERT INTO claim_limitations (
                        limitation_id, claim_id,
                        limitation_order, normalized_text
                    )
                    VALUES %s
                    ON CONFLICT (claim_id, limitation_order) DO NOTHING
                """, limitations_data)

            conn.commit()
            success += len(batch)
            print(f"✅ 배치 {batch_idx // batch_size + 1} 완료: {len(batch)}건 (신규 doc: {len(doc_data)}, 신규 claim: {len(claims_data)}, 신규 limit: {len(limitations_data)})")

        except Exception as e:
            print(f"❌ 배치 저장 실패 (시작: {batch_idx}): {e}")
            conn.rollback()
            fail += len(batch)

    cursor.close()
    conn.close()
    print(f"✅ 저장 완료: {success}건 성공 / {fail}건 실패")

# 정합성 검증 함수
def verify():
    conn = connect_db()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM documents WHERE source_type = 'patent'")
    doc_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM patent_claims")
    claim_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM patent_claims WHERE is_independent = TRUE")
    independent_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM claim_limitations")
    limitation_count = cursor.fetchone()[0]

    cursor.close()
    conn.close()

    print("\n=== 저장 결과 확인 ===")
    print(f"documents 테이블        : {doc_count}건")
    print(f"patent_claims 테이블    : {claim_count}건")
    print(f"독립항                  : {independent_count}건")
    print(f"claim_limitations 테이블: {limitation_count}건")

    if claim_count > 0:
        ratio = independent_count / claim_count * 100
        status = "✅ 정상" if 10 <= ratio <= 30 else "⚠️  비정상 (정규식 확인 필요)"
        print(f"독립항 비율             : {ratio:.1f}% {status}")

    print("========================")
    print(f"{doc_count}건 적재 완료")

def list_s3_files(prefix='raw/patents/'):
    s3 = connect_s3()
    response = s3.list_objects_v2(Bucket=os.getenv('S3_BUCKET_NAME'), Prefix=prefix)
    objects = response.get('Contents', [])
    if not objects:
        raise FileNotFoundError(f'S3에 {prefix} 하위 파일이 없습니다.')
    return sorted(o['Key'] for o in objects)


if __name__ == "__main__":
    files = list_s3_files()
    print(f'✅ S3 파일 {len(files)}개 발견\n')
    for filename in files:
        print(f'▶ 처리 중: {filename}')
        rows = load_from_s3(filename)
        save_to_db(rows)
    verify()