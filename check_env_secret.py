"""Check if AWS_SECRET_ACCESS_KEY is loaded correctly"""

import os
from dotenv import load_dotenv
load_dotenv(override=True)

secret = os.getenv('AWS_SECRET_ACCESS_KEY', '')
print(f'\nSecret loaded:')
print(f'  Length: {len(secret)}')
print(f'  Value: {secret}')
print(f'  Starts with: {secret[:10]}...')
print()

if len(secret) < 30:
    print('⚠️  SECRET_ACCESS_KEY 길이가 짧음 (정상: ~40자)')
else:
    print('✓ SECRET_ACCESS_KEY 길이 정상')
