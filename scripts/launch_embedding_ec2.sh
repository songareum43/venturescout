#!/bin/bash
# VentureScout 임베딩 EC2 Spot 인스턴스 실행 스크립트
# 사용법: bash scripts/launch_embedding_ec2.sh
# 비용: 약 $0.06/회 (g4dn.xlarge Spot, ~13분)

set -euo pipefail

REGION="ap-northeast-1"
AMI_ID="ami-0b5c8dc0357475c97"        # Deep Learning OSS Nvidia PyTorch 2.11 AL2023 (2026-06-13)
INSTANCE_TYPE="g4dn.xlarge"
KEY_NAME="key-ju-tokyo"
SUBNET_ID="subnet-04b411a55e4cf929d"  # ap-northeast-1c, IGW 연결
SG_ID="sg-05389f3946d32d54e"          # venturescout-embedding-sg
INSTANCE_PROFILE="venturescout-embedding-role"
SPOT_MAX_PRICE="0.35"                  # On-Demand $0.526 대비 여유 있게 설정
S3_RUNNER="s3://venturescout-scripts-827913617635/ec2_embedding_runner.py"
SSM_PARAM="/venturescout/db/dsn"

echo "=== VentureScout Embedding EC2 ==="
echo "  인스턴스: ${INSTANCE_TYPE} (Spot, 최대 \$${SPOT_MAX_PRICE}/hr)"
echo "  예상 비용: ~\$0.06 (약 13분)"
echo ""

# 러너 스크립트를 최신으로 S3 동기화
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
aws s3 cp "${SCRIPT_DIR}/ec2_embedding_runner.py" "${S3_RUNNER}" --region "${REGION}"
echo "[1/4] S3 업로드 완료"

# User Data 스크립트 (EC2 시작 시 자동 실행)
USER_DATA=$(base64 <<'USERDATA'
#!/bin/bash
set -euo pipefail
exec > >(tee /var/log/embedding.log | logger -t venturescout) 2>&1

echo "=== VentureScout Embedding Start: $(date) ==="

# pip 의존성 설치
pip3 install --quiet --upgrade pip
pip3 install --quiet \
  "psycopg2-binary>=2.9" \
  "pgvector>=0.3" \
  "sentence-transformers>=3.0" \
  "numpy<2"

echo "[deps] 설치 완료"

# SSM에서 DB DSN 가져오기
export DB_DSN=$(aws ssm get-parameter \
  --region ap-northeast-1 \
  --name /venturescout/db/dsn \
  --with-decryption \
  --query Parameter.Value \
  --output text)

echo "[ssm] DB DSN 로드 완료"

# 러너 스크립트 다운로드
aws s3 cp s3://venturescout-scripts-827913617635/ec2_embedding_runner.py /home/ec2-user/runner.py
echo "[s3] 러너 스크립트 다운로드 완료"

# 임베딩 실행
python3 /home/ec2-user/runner.py

echo "=== 완료: $(date) ==="

# 자동 종료 (tag:Project=venturescout 이어야 IAM 정책 통과)
INSTANCE_ID=$(curl -s http://169.254.169.254/latest/meta-data/instance-id)
aws ec2 terminate-instances --region ap-northeast-1 --instance-ids "$INSTANCE_ID"
USERDATA
)

# Spot 인스턴스 실행
echo "[2/4] 인스턴스 시작 중..."
INSTANCE_ID=$(aws ec2 run-instances \
  --region "${REGION}" \
  --image-id "${AMI_ID}" \
  --instance-type "${INSTANCE_TYPE}" \
  --key-name "${KEY_NAME}" \
  --subnet-id "${SUBNET_ID}" \
  --security-group-ids "${SG_ID}" \
  --iam-instance-profile "Name=${INSTANCE_PROFILE}" \
  --instance-market-options "{\"MarketType\":\"spot\",\"SpotOptions\":{\"MaxPrice\":\"${SPOT_MAX_PRICE}\",\"SpotInstanceType\":\"one-time\"}}" \
  --associate-public-ip-address \
  --user-data "${USER_DATA}" \
  --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":100,"VolumeType":"gp3","DeleteOnTermination":true}}]' \
  --tag-specifications \
    "ResourceType=instance,Tags=[{Key=Name,Value=venturescout-embedding},{Key=Project,Value=venturescout}]" \
    "ResourceType=volume,Tags=[{Key=Project,Value=venturescout}]" \
  --query 'Instances[0].InstanceId' \
  --output text)

echo "[3/4] 인스턴스 시작됨: ${INSTANCE_ID}"

# 실행 상태 대기
echo "[4/4] running 상태 대기 중..."
aws ec2 wait instance-running --region "${REGION}" --instance-ids "${INSTANCE_ID}"

echo ""
echo "========================================="
echo " 인스턴스 ID: ${INSTANCE_ID}"
echo " 로그 확인  : AWS Console → EC2 → ${INSTANCE_ID} → Actions → Monitor → Get system log"
echo " 종료 확인  : 임베딩 완료 후 자동 terminate"
echo " 예상 완료  : 약 15분 후"
echo "========================================="
