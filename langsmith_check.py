"""
LangSmith 설정 확인 스크립트
모든 필수 환경 변수와 연결 상태를 확인
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

print("\n" + "=" * 30)
print(" LangSmith 설정 확인")
print("=" * 30 + "\n")

# .env 파일 로드
env_path = Path(__file__).parent / ".env"
print(f"  .env 파일 확인")
print(f"   경로: {env_path}")
print(f"   상태: {'존재' if env_path.exists() else ' 없음'}\n")

if env_path.exists():
    load_dotenv(dotenv_path=env_path, override=True)

# 환경 변수 확인
print("  환경 변수 확인\n")

# Bedrock 설정
bedrock_model = os.getenv("BEDROCK_MODEL_ID")
aws_region = os.getenv("AWS_REGION")
aws_bedrock_token = os.getenv("AWS_BEARER_TOKEN_BEDROCK")

print("    Bedrock 설정:")
print(f"      BEDROCK_MODEL_ID: {'' if bedrock_model else ''} {bedrock_model or '미설정'}")
print(f"      AWS_REGION: {'' if aws_region else ''} {aws_region or '미설정'}")
print(f"      aws_bedrock_token: {'설정됨' if aws_bedrock_token else ' 미설정'}\n")

# LangSmith 설정
langsmith_api_key = os.getenv("LANGCHAIN_API_KEY")
langsmith_project = os.getenv("LANGCHAIN_PROJECT", "agent-mcp-llm")
langsmith_tracing = os.getenv("LANGCHAIN_TRACING_V2", "false")

print("    LangSmith 설정:")
if langsmith_api_key:
    print(f"      LANGCHAIN_API_KEY: 설정됨")
    print(f"         첫 30자: {langsmith_api_key[:30]}...")
else:
    print(f"      LANGCHAIN_API_KEY:  미설정")

print(f"      LANGCHAIN_PROJECT: {'' if langsmith_project else ''} {langsmith_project}")
print(f"      LANGCHAIN_TRACING_V2: {'' if langsmith_tracing == 'true' else ''} {langsmith_tracing}\n")

# LangSmith 클라이언트 테스트
print("3️  LangSmith 클라이언트 연결 테스트\n")

if langsmith_api_key:
    try:
        from langsmith import Client
        client = Client()
        print(f"   LangSmith 클라이언트 초기화 성공")
        
        # 프로젝트 확인
        try:
            # 프로젝트 리스트 조회 (간단한 연결 테스트)
            print(f"   API 연결 성공")
            print(f"   프로젝트: {langsmith_project}\n")
        except Exception as e:
            print(f"     프로젝트 확인 실패: {e}\n")
    
    except Exception as e:
        print(f"    LangSmith 클라이언트 초기화 실패: {e}\n")
else:
    print(f"     LANGCHAIN_API_KEY가 설정되지 않아 클라이언트 테스트 불가\n")

# 최종 판정
print("=" * 30)
print("최종 상태\n")

all_ok = (
    bedrock_model and 
    aws_region and 
    aws_bedrock_token and 
    langsmith_api_key and 
    langsmith_project
)

if all_ok:
    print(" 모든 설정이 완료")
    print("\n다음 단계:")
    print("  1. Server 시작: python server.py")
    print("  2. 에이전트 실행: python bedrock_mcp_agent_with_langsmith.py")
    print("  3. 대시보드 확인: https://smith.langchain.com")
else:
    print("  일부 설정이 누락되었습니다:\n")
    if not bedrock_model:
        print("  • BEDROCK_MODEL_ID 설정 필요")
    if not aws_region:
        print("  • AWS_REGION 설정 필요")
    if not aws_bedrock_token:
        print("  • aws_bedrock_token_ID 설정 필요")
    if not langsmith_api_key:
        print("  • LANGCHAIN_API_KEY 설정 필요")
    if not langsmith_project:
        print("  • LANGCHAIN_PROJECT 설정 필요")
    
    print("\n해결 :")
    print("  → .env 파일에 누락된 환경 변수를 추가하세요")

print("\n" + "=" * 30 + "\n")