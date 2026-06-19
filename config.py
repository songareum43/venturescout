"""
Config — 데이터 소스(USPTO/KIPRIS)에 따라 임베딩 모델이 자동 결정됨.
DATA_SOURCE 환경변수 하나만 바꾸면 임베딩 모델/언어가 같이 바뀜.
"""
import os
from dataclasses import dataclass, field
from urllib.parse import quote_plus
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # ── 데이터 소스 ──────────────────────────────────────────
    data_source: str = os.getenv("DATA_SOURCE", "USPTO")  # "USPTO" | "KIPRIS"

    # ── DB (.env.example: POSTGRES_*, DATABASE_URL) ───────────
    db_host: str = os.getenv("POSTGRES_HOST", "localhost")
    db_port: int = int(os.getenv("POSTGRES_PORT", "5432"))
    db_name: str = os.getenv("POSTGRES_DB", "venturescout")
    db_user: str = os.getenv("POSTGRES_USER", "vs")
    db_password: str = os.getenv("POSTGRES_PASSWORD", "")
    db_connect_timeout: int = int(os.getenv("DB_CONNECT_TIMEOUT", "10"))

    # ── 임베딩 (소스 결정 전 기본값, __post_init__에서 덮어씀) ────
    embedding_model: str = ""
    embedding_dim: int = int(os.getenv("EMBEDDING_DIM", "768"))
    max_tokens: int = 512

    # ── 검색 가중치 ───────────────────────────────────────────
    vector_weight: float = 0.6
    keyword_weight: float = 0.4
    top_k_fetch: int = 20   # DB에서 가져올 후보 수
    top_k_return: int = 10  # rerank 후 반환 수

    # ── rerank 가중치 ─────────────────────────────────────────
    rerank_relevance_w: float = 0.4
    rerank_reliability_w: float = 0.3
    rerank_freshness_w: float = 0.1
    rerank_contradiction_w: float = 0.2

    # ── 소스별 신뢰도 (documents.reliability_score 기본값/폴백) ──
    source_reliability: dict = field(default_factory=lambda: {
        "patent":          0.9,
        "seed_review":     0.6,
        "seed_competitor": 0.6,
        "seed_pricing":    0.6,
        "web":             0.4,
    })

    # ── AWS Bedrock ───────────────────────────────────────────
    bedrock_region: str = os.getenv("AWS_REGION", "us-east-1")
    bedrock_model_id: str = os.getenv(
        "BEDROCK_SONNET_MODEL_ID",
        os.getenv(
            "BEDROCK_MODEL_ID",
            "us.anthropic.claude-sonnet-4-6",
        ),
    )

    def __post_init__(self):
        # 데이터 소스에 따라 임베딩 모델 자동 결정
        if not self.embedding_model:
            if self.data_source == "KIPRIS":
                # KorPatBERT: 사용신청·승인 필요. 승인 전 폴백은 PatentSBERTa_V2
                self.embedding_model = os.getenv(
                    "EMBEDDING_MODEL",
                    "snunlp/KorPatBERT"          # 폴백: "bongsoo/patent-sbert-v2"
                )
            else:
                # USPTO / Google Patents BigQuery 경로
                self.embedding_model = os.getenv(
                    "EMBEDDING_MODEL",
                    "AI-Growth-Lab/PatentSBERTa"
                )

    @property
    def db_dsn(self) -> str:
        """DB 연결 문자열을 우선순위 순으로 반환한다.

        우선순위:
        1. DATABASE_URL — 완성된 URL이 있으면 그대로 쓴다.
        2. RDS_SECRET_ARN — AWS Secrets Manager에서 비밀번호를 런타임에 가져온다.
           .env에 비밀번호를 평문으로 저장하지 않아도 되므로 운영 환경 기본 방식이다.
        3. POSTGRES_* 낱개 변수 — 로컬 Docker/로컬 RDS 직접 연결 시 사용한다.
        """
        # 1순위: DATABASE_URL이 있으면 파싱 없이 바로 반환
        url = os.getenv("DATABASE_URL")
        if url:
            return url

        # 2순위: Secrets Manager ARN → boto3로 런타임 조회
        # 비밀번호를 .env에 저장하지 않고 IAM Role/Instance Profile로 접근한다.
        secret_arn = os.getenv("RDS_SECRET_ARN")
        if secret_arn:
            import json as _j
            import boto3
            region = secret_arn.split(":")[3]   # ARN 형식: arn:aws:...:region:account:...
            client = boto3.client("secretsmanager", region_name=region)
            secret = _j.loads(
                client.get_secret_value(SecretId=secret_arn)["SecretString"]
            )
            # RDS 관리형 시크릿은 host/port/dbname까지 포함하는 경우가 있어
            # .env의 POSTGRES_* 값을 폴백으로 사용한다.
            host = secret.get("host", self.db_host)
            port = secret.get("port", self.db_port)
            dbname = secret.get("dbname", self.db_name)
            return (
                f"postgresql://{secret['username']}:{quote_plus(secret['password'])}"
                f"@{host}:{port}/{dbname}"
            )

        # 3순위: POSTGRES_* 낱개 변수 조합
        # 비밀번호에 URI 예약문자(#, :, ?, @ 등)가 있어도 안전하게 인코딩
        return (
            f"postgresql://{self.db_user}:{quote_plus(self.db_password)}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def is_korean(self) -> bool:
        return self.data_source == "KIPRIS"


config = Config()
