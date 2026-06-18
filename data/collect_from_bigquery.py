'''Bigquery -> s3 저장'''

# 모듈 가져오기
from google.cloud import bigquery
import boto3
import json
from datetime import datetime
from dotenv import load_dotenv
import os

# 환경변수 로드
load_dotenv()

# s3 클라이언트 함수
def connect_s3():
    return boto3.client('s3',
        region_name=os.getenv('AWS_REGION')
    )

START_YEAR = 2021                  # 이 연도 미만은 수집하지 않음
END_YEAR   = 2024                  # datetime.now().year  -> 현재 연도까지 자동 적용

def get_collected_years(prefix='raw/patents/'):
    """S3에 이미 저장된 파일에서 수집 완료된 연도 집합을 반환"""
    s3 = connect_s3()
    response = s3.list_objects_v2(Bucket=os.getenv('S3_BUCKET_NAME'), Prefix=prefix) # 파일 목록 가져오기
    collected = set()
    for obj in response.get('Contents', []):
        key = obj['Key']  # raw/patents/patents_20210101_20211231.json
        parts = key.split('_')
        if len(parts) >= 3:
            try:
                collected.add(int(parts[-2][:4]))  # 시작 날짜의 연도
            except (ValueError, IndexError):
                pass
    return collected

def fetch_year(client, year):
    """연도 하나를 BigQuery에서 조회하여 S3에 저장"""
    start = f"{year}0101"
    end   = f"{year}1231"

    query = f"""
    SELECT DISTINCT
    pub.publication_number,
    (SELECT text FROM UNNEST(pub.title_localized)
    WHERE language = 'en' LIMIT 1) AS title,
    (SELECT text FROM UNNEST(pub.abstract_localized)
    WHERE language = 'en' LIMIT 1) AS abstract,
    pub.filing_date,
    pub.grant_date,
    pub.assignee_harmonized[SAFE_OFFSET(0)].name AS assignee,
    (SELECT cpc.code FROM UNNEST(pub.cpc) AS cpc
    WHERE cpc.code LIKE 'G06Q30%' LIMIT 1) AS cpc_code,
    (SELECT claim.text FROM UNNEST(pub.claims_localized) AS claim
    WHERE claim.language = 'en' LIMIT 1) AS claim_text
    FROM `patents-public-data.patents.publications` AS pub
    WHERE EXISTS (
    SELECT 1 FROM UNNEST(pub.cpc) AS cpc
    WHERE cpc.code LIKE 'G06Q30%'
    )
    AND pub.country_code = 'US'
    AND pub.filing_date >= {start}
    AND pub.filing_date <= {end}
    """

    print(f"[{year}] BigQuery 쿼리 실행 중...")
    rows = [dict(row) for row in client.query(query).result()]
    print(f"[{year}] ✅ {len(rows)}건 가져옴")

    s3 = connect_s3()
    filename = f"raw/patents/patents_{start}_{end}.json"

    s3.put_object(
        Bucket=os.getenv('S3_BUCKET_NAME'),
        Key=filename,
        Body=json.dumps(rows, default=str, ensure_ascii=False).encode('utf-8'),
        ContentType='application/json'
    )
    print(f"[{year}] ✅ S3 저장 완료: {filename}")
    return filename

def fetch_and_backup():
    """START_YEAR~END_YEAR 범위에서 S3에 없는 연도만 BigQuery 조회 후 저장"""
    collected = get_collected_years()
    targets = [y for y in range(START_YEAR, END_YEAR + 1) if y not in collected]

    if not targets:
        print(f"✅ {START_YEAR}~{END_YEAR} 전체 수집 완료 상태입니다. 건너뜁니다.")
        return []

    print(f"수집 대상 연도: {targets} (이미 수집됨: {sorted(collected)})")
    client = bigquery.Client()
    saved = []
    for year in targets:
        filename = fetch_year(client, year)
        saved.append(filename)
    print(f"\n✅ 전체 완료: {len(saved)}개 파일 저장")
    return saved

if __name__ == "__main__":
    fetch_and_backup()
