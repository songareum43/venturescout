"""Test AWS credentials and Bedrock access"""

import os
from dotenv import load_dotenv
load_dotenv(override=True)

import boto3

key = os.getenv('AWS_ACCESS_KEY_ID')
secret = os.getenv('AWS_SECRET_ACCESS_KEY')
region = os.getenv('AWS_REGION', 'ap-northeast-1')

print('\n✓ Credentials loaded:')
print(f'  Key: {key[:20]}...')
print(f'  Region: {region}\n')

try:
    sts = boto3.client('sts', region_name=region)
    identity = sts.get_caller_identity()
    print('✓ AWS STS 인증 성공!')
    print(f'  Account: {identity["Account"]}')
    print(f'  User: {identity["Arn"]}\n')
except Exception as e:
    print(f'✗ AWS 인증 실패: {e}\n')
    exit(1)

try:
    bedrock = boto3.client('bedrock-runtime', region_name=region)
    print('✓ Bedrock 클라이언트 생성 성공!')

    # 간단한 테스트 호출
    response = bedrock.converse(
        modelId='anthropic.claude-sonnet-4-6',
        system=[{'text': 'You are a helpful assistant.'}],
        messages=[{'role': 'user', 'content': [{'text': 'Hello'}]}],
        inferenceConfig={'temperature': 0.1, 'maxTokens': 100},
    )

    usage = response.get('usage', {})
    print('✓ Bedrock 호출 성공!')
    print(f'  Input tokens: {usage.get("inputTokens", 0)}')
    print(f'  Output tokens: {usage.get("outputTokens", 0)}\n')

except Exception as e:
    print(f'✗ Bedrock 호출 실패: {e}\n')
    exit(1)

print('✓ 모든 테스트 통과!')
