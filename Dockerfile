# 컨테이너는 Python 3.11 (로컬 3.14와 분리)
FROM python:3.11-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 임베딩 모델(PatentSBERTa)을 빌드 시점에 미리 받아 이미지에 굽는다.
# → RETRIEVAL=live 첫 검색에서 런타임 HF Hub 다운로드(수백 MB)로 멈추던 문제 제거.
#   (코드 COPY 앞에 둬서 소스 변경이 이 무거운 레이어를 무효화하지 않게 함)
RUN python -c "from sentence_transformers import SentenceTransformer; from transformers import AutoTokenizer; m='AI-Growth-Lab/PatentSBERTa'; SentenceTransformer(m); AutoTokenizer.from_pretrained(m)"

COPY . .
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
