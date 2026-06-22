"""Test Bedrock with explicitly passed credentials"""

import os
from dotenv import load_dotenv
load_dotenv(override=True)

import boto3

# .env에서 직접 읽기
access_key = os.getenv('AWS_ACCESS_KEY_ID')
secret_key = os.getenv('AWS_SECRET_ACCESS_KEY')
region = os.getenv('AWS_REGION', 'ap-northeast-1')
model_id = os.getenv('BEDROCK_MODEL_ID', 'anthropic.claude-sonnet-4-6')

print(f'\n자격증명 로드:')
print(f'  Access Key: {access_key[:20]}...')
print(f'  Secret Key: {secret_key[:20]}...')
print(f'  Region: {region}')
print(f'  Model: {model_id}\n')

# 명시적으로 자격증명 전달
try:
    bedrock = boto3.client(
        'bedrock-runtime',
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )

    print('✓ Bedrock 클라이언트 생성 성공\n')

    # 간단한 호출
    print('Bedrock 호출 중...')
    response = bedrock.converse(
        modelId=model_id,
        system=[{'text': 'You are a helpful assistant.'}],
        messages=[{'role': 'user', 'content': [{'text': 'Hello, what is 2+2?'}]}],
        inferenceConfig={'temperature': 0.1, 'maxTokens': 100},
    )

    usage = response.get('usage', {})
    print(f'✓ Bedrock 호출 성공!\n')
    print(f'Response:')
    print(f'  Input tokens: {usage.get("inputTokens", 0)}')
    print(f'  Output tokens: {usage.get("outputTokens", 0)}')
    print(f'  Total: {usage.get("inputTokens", 0) + usage.get("outputTokens", 0)}\n')

except Exception as e:
    print(f'✗ 에러: {type(e).__name__}: {e}\n')
    import traceback
    traceback.print_exc()
