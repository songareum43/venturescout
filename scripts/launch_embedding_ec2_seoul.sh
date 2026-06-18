#!/bin/bash
# VentureScout 임베딩 EC2 Spot — 서울 리전 (빠른 작업용)
# 사용법: bash scripts/launch_embedding_ec2_seoul.sh
#
# 구성:
#   - g5.xlarge Spot (A10G 24GB VRAM) — 가장 빠른 임베딩
#   - 서울(ap-northeast-2), RDS는 도쿄(ap-northeast-1) — 크로스리전
#   - EC2 부팅 시 공인 IP → 도쿄 RDS SG 자동 등록, 완료 후 자동 해제
#   - 임베딩 완료 후 인스턴스 자동 terminate
# 비용: 약 $0.30-0.40 (g5.xlarge Spot ~0.5/hr × ~40분)

set -euo pipefail

REGION="ap-northeast-2"
AMI_ID="ami-05bfd36e7686b161f"           # Deep Learning OSS Nvidia Driver AMI GPU PyTorch 2.7, Ubuntu 22.04 (2026-04-28)
INSTANCE_TYPE="g5.xlarge"               # A10G 24GB VRAM — 최고 속도
KEY_NAME="key-ju"
SUBNET_ID="subnet-0809df27db812cbd0"    # ap-northeast-2a, public
SG_ID="sg-05b1838ab142727f1"            # venturescout-embedding-sg (서울, outbound only)
INSTANCE_PROFILE="venturescout-embedding-role"
SPOT_MAX_PRICE="0.80"                   # On-Demand $1.006 대비 여유 있게 설정
S3_RUNNER="s3://venturescout-scripts-827913617635/ec2_embedding_runner.py"
SSM_REGION="ap-northeast-1"            # SSM 파라미터는 도쿄에 있음
SSM_PARAM="/venturescout/db/dsn"
RDS_SG_ID="sg-00de9e0cff246764e"        # venturescout-rds-sg (도쿄)
RDS_SG_REGION="ap-northeast-1"

echo "=== VentureScout Embedding EC2 (Seoul) ==="
echo "  인스턴스: ${INSTANCE_TYPE} Spot (A10G, 최대 \$${SPOT_MAX_PRICE}/hr)"
echo "  예상 비용: ~\$0.35 (약 40분)"
echo ""

# 러너 스크립트를 최신으로 S3 동기화
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
aws s3 cp "${SCRIPT_DIR}/ec2_embedding_runner.py" "${S3_RUNNER}" --region "${SSM_REGION}"
echo "[1/4] S3 업로드 완료"

# User Data 스크립트 (EC2 시작 시 자동 실행)
# 주의: heredoc 내부에서 변수 확장이 필요한 부분은 $로, 리터럴 달러는 \$로 처리
USER_DATA=$(base64 <<USERDATA
#!/bin/bash
set -euo pipefail
exec > >(tee /var/log/embedding.log | logger -t venturescout) 2>&1

echo "=== VentureScout Embedding Start: \$(date) ==="

# pip 의존성 설치 (Ubuntu DL AMI는 pip3 사용)
pip3 install --quiet --upgrade pip
pip3 install --quiet \
  "psycopg2-binary>=2.9" \
  "pgvector>=0.3" \
  "sentence-transformers>=3.0" \
  "numpy<2"

echo "[deps] 설치 완료"

# SSM에서 DB DSN 가져오기 (도쿄 리전)
export DB_DSN=\$(aws ssm get-parameter \
  --region ${SSM_REGION} \
  --name ${SSM_PARAM} \
  --with-decryption \
  --query Parameter.Value \
  --output text)
echo "[ssm] DB DSN 로드 완료"

# 이 EC2의 공인 IP를 도쿄 RDS 보안그룹에 등록
MY_IP=\$(curl -s https://checkip.amazonaws.com)
MY_CIDR="\${MY_IP}/32"
RULE_DESC="ec2-embedding-seoul"

# 중복 방지: 같은 description의 기존 규칙 먼저 삭제
OLD_RULES=\$(aws ec2 describe-security-group-rules \
  --region ${RDS_SG_REGION} \
  --filters "Name=group-id,Values=${RDS_SG_ID}" \
  --query "SecurityGroupRules[?Description=='\${RULE_DESC}' && IpProtocol=='tcp' && FromPort==\`5432\`].SecurityGroupRuleId" \
  --output text 2>/dev/null || true)
for rid in \$OLD_RULES; do
  aws ec2 revoke-security-group-ingress \
    --region ${RDS_SG_REGION} \
    --group-id ${RDS_SG_ID} \
    --security-group-rule-ids "\$rid" >/dev/null
done

aws ec2 authorize-security-group-ingress \
  --region ${RDS_SG_REGION} \
  --group-id ${RDS_SG_ID} \
  --ip-permissions "IpProtocol=tcp,FromPort=5432,ToPort=5432,IpRanges=[{CidrIp=\${MY_CIDR},Description=\${RULE_DESC}}]" >/dev/null

echo "[rds-sg] \${MY_CIDR} 등록 완료 (도쿄 RDS SG)"

# 러너 스크립트 다운로드
aws s3 cp ${S3_RUNNER} /home/ubuntu/runner.py --region ${SSM_REGION}
echo "[s3] 러너 스크립트 다운로드 완료"

# 임베딩 실행
python3 /home/ubuntu/runner.py
EXIT_CODE=\$?

echo "=== 완료: \$(date) (exit=\${EXIT_CODE}) ==="

# RDS SG 정리 (등록한 IP 규칙 제거)
DONE_RULES=\$(aws ec2 describe-security-group-rules \
  --region ${RDS_SG_REGION} \
  --filters "Name=group-id,Values=${RDS_SG_ID}" \
  --query "SecurityGroupRules[?Description=='\${RULE_DESC}' && IpProtocol=='tcp' && FromPort==\`5432\`].SecurityGroupRuleId" \
  --output text 2>/dev/null || true)
for rid in \$DONE_RULES; do
  aws ec2 revoke-security-group-ingress \
    --region ${RDS_SG_REGION} \
    --group-id ${RDS_SG_ID} \
    --security-group-rule-ids "\$rid" >/dev/null
done
echo "[rds-sg] IP 규칙 해제 완료"

# 자동 종료
INSTANCE_ID=\$(curl -s http://169.254.169.254/latest/meta-data/instance-id)
aws ec2 terminate-instances --region ${REGION} --instance-ids "\${INSTANCE_ID}"
USERDATA
)

# Spot 시도 → MaxSpotInstanceCountExceeded 시 On-Demand 자동 폴백
echo "[2/4] 인스턴스 시작 중 (Spot 우선)..."
COMMON_ARGS=(
  --region "${REGION}"
  --image-id "${AMI_ID}"
  --instance-type "${INSTANCE_TYPE}"
  --key-name "${KEY_NAME}"
  --subnet-id "${SUBNET_ID}"
  --security-group-ids "${SG_ID}"
  --iam-instance-profile "Name=${INSTANCE_PROFILE}"
  --associate-public-ip-address
  --user-data "${USER_DATA}"
  --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":100,"VolumeType":"gp3","DeleteOnTermination":true}}]'
  --tag-specifications
    "ResourceType=instance,Tags=[{Key=Name,Value=venturescout-embedding},{Key=Project,Value=venturescout}]"
    "ResourceType=volume,Tags=[{Key=Project,Value=venturescout}]"
  --query 'Instances[0].InstanceId'
  --output text
)

INSTANCE_ID=$(aws ec2 run-instances "${COMMON_ARGS[@]}" \
  --instance-market-options "{\"MarketType\":\"spot\",\"SpotOptions\":{\"MaxPrice\":\"${SPOT_MAX_PRICE}\",\"SpotInstanceType\":\"one-time\"}}" \
  2>&1) || true

if echo "$INSTANCE_ID" | grep -q "MaxSpotInstanceCountExceeded\|InsufficientInstanceCapacity"; then
  echo "  Spot 쿼터 초과 → On-Demand로 폴백"
  INSTANCE_ID=$(aws ec2 run-instances "${COMMON_ARGS[@]}")
fi

echo "[3/4] 인스턴스 시작됨: ${INSTANCE_ID}"

# 실행 상태 대기
echo "[4/4] running 상태 대기 중..."
aws ec2 wait instance-running --region "${REGION}" --instance-ids "${INSTANCE_ID}"

# 공인 IP 출력
PUBLIC_IP=$(aws ec2 describe-instances \
  --region "${REGION}" \
  --instance-ids "${INSTANCE_ID}" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' \
  --output text)

echo ""
echo "========================================="
echo " 인스턴스 ID : ${INSTANCE_ID}"
echo " 공인 IP     : ${PUBLIC_IP}"
echo " 로그 확인   : aws ec2 get-console-output --region ${REGION} --instance-id ${INSTANCE_ID} --latest --output text"
echo " 종료 확인   : 임베딩 완료 후 자동 terminate (RDS SG도 자동 정리)"
echo " 예상 완료   : 약 40분 후"
echo "========================================="
