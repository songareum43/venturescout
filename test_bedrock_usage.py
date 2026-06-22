"""Test Bedrock response to see if usage is included"""

import os
from dotenv import load_dotenv
load_dotenv(override=True)

import boto3
from botocore.config import Config

config = Config(
    read_timeout=int(os.getenv('BEDROCK_READ_TIMEOUT', '300')),
    connect_timeout=10,
    retries={'max_attempts': 2},
)

client = boto3.client(
    'bedrock-runtime',
    region_name=os.getenv('AWS_REGION', 'ap-northeast-1'),
    config=config,
)

print("\n🔍 Bedrock converse API 테스트\n")

response = client.converse(
    modelId='anthropic.claude-sonnet-4-6',
    system=[{'text': '너는 한 단어로만 답변한다.'}],
    messages=[{'role': 'user', 'content': [{'text': '2+2는?'}]}],
    inferenceConfig={'temperature': 0.1, 'maxTokens': 100},
)

print("✓ API 호출 성공\n")
print(f"응답 키: {list(response.keys())}\n")

usage = response.get('usage', {})
print(f"usage 필드 내용:")
print(f"  inputTokens: {usage.get('inputTokens')}")
print(f"  outputTokens: {usage.get('outputTokens')}")
print(f"  total: {usage.get('inputTokens', 0) + usage.get('outputTokens', 0)}")
print()
